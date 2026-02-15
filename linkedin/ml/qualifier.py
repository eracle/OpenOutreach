# linkedin/ml/qualifier.py
"""QualificationScorer: embedding-based profile qualification with Bootstrap Ensemble."""
from __future__ import annotations

import logging

import jinja2
import numpy as np
from pydantic import BaseModel, Field

from linkedin.conf import ASSETS_DIR, CAMPAIGN_CONFIG, CAMPAIGN_OBJECTIVE_FILE, PRODUCT_DOCS_FILE

logger = logging.getLogger(__name__)


class QualificationDecision(BaseModel):
    """Structured LLM output for lead qualification."""
    qualified: bool = Field(description="True if the profile is a good prospect, False otherwise")
    reason: str = Field(description="Brief explanation for the decision")


def qualify_profile_llm(profile_text: str) -> tuple[int, str]:
    """Call LLM to qualify a profile. Returns (label, reason).

    label: 1 = accept, 0 = reject.
    """
    from langchain_openai import ChatOpenAI

    from linkedin.conf import AI_MODEL, LLM_API_KEY, LLM_API_BASE

    if LLM_API_KEY is None:
        raise ValueError("LLM_API_KEY is not set in the environment or config.")

    template_dir = ASSETS_DIR / "templates" / "prompts"
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(template_dir)))
    template = env.get_template("qualify_lead.j2")

    if not PRODUCT_DOCS_FILE.exists():
        raise FileNotFoundError(f"Product docs not found: {PRODUCT_DOCS_FILE}")
    if not CAMPAIGN_OBJECTIVE_FILE.exists():
        raise FileNotFoundError(f"Campaign objective not found: {CAMPAIGN_OBJECTIVE_FILE}")

    product_docs = PRODUCT_DOCS_FILE.read_text(encoding="utf-8")
    campaign_objective = CAMPAIGN_OBJECTIVE_FILE.read_text(encoding="utf-8")

    prompt = template.render(
        product_docs=product_docs,
        campaign_objective=campaign_objective,
        profile_text=profile_text,
    )

    llm = ChatOpenAI(model=AI_MODEL, temperature=0.7, api_key=LLM_API_KEY, base_url=LLM_API_BASE)
    structured_llm = llm.with_structured_output(QualificationDecision)
    decision = structured_llm.invoke(prompt)

    label = 1 if decision.qualified else 0
    return (label, decision.reason)


class QualificationScorer:
    """Embedding-based profile scorer with Bootstrap Ensemble.

    Replaces ProfileScorer. Same public interface: train(), score_profiles(), explain_profile().
    """

    def __init__(self, seed: int = 42):
        self._rng = np.random.RandomState(seed)
        self._ensemble = None
        self._trained = False
        self._labels_at_last_train = 0
        self._cfg = CAMPAIGN_CONFIG

    def train(self) -> bool:
        """Train Bootstrap Ensemble of HistGradientBoosting classifiers.

        Returns False if insufficient or imbalanced data.
        """
        from sklearn.ensemble import BaggingClassifier, HistGradientBoostingClassifier
        from sklearn.model_selection import StratifiedKFold, cross_val_score

        from linkedin.ml.embeddings import count_labeled, get_labeled_data

        counts = count_labeled()
        min_samples = self._cfg["qualification_min_training_samples"]
        min_ratio = self._cfg["qualification_min_class_ratio"]
        n_estimators = self._cfg["qualification_n_estimators"]

        if counts["total"] < min_samples:
            logger.debug(
                "Only %d labeled samples — need at least %d for training",
                counts["total"], min_samples,
            )
            return False

        # Check class balance
        if counts["total"] > 0:
            minority = min(counts["positive"], counts["negative"])
            ratio = minority / counts["total"]
            if ratio < min_ratio:
                logger.debug(
                    "Class ratio %.2f below minimum %.2f — skipping training",
                    ratio, min_ratio,
                )
                return False

        X, y = get_labeled_data()
        if len(X) < min_samples or len(np.unique(y)) < 2:
            return False

        # Compute sample weights inversely proportional to class frequency
        classes, class_counts = np.unique(y, return_counts=True)
        total = len(y)
        weight_map = {c: total / (len(classes) * cnt) for c, cnt in zip(classes, class_counts)}
        sample_weight = np.array([weight_map[yi] for yi in y])

        base_clf = HistGradientBoostingClassifier(
            max_iter=200,
            max_depth=4,
            min_samples_leaf=5,
            l2_regularization=1.0,
            random_state=42,
        )

        ensemble = BaggingClassifier(
            estimator=base_clf,
            n_estimators=n_estimators,
            max_samples=0.8,
            bootstrap=True,
            random_state=42,
        )

        # Cross-validation
        n_splits = min(5, max(2, len(X) // 10))
        cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
        scores = cross_val_score(ensemble, X, y, cv=cv, scoring="roc_auc")
        logger.info(
            "Qualification CV ROC-AUC (%d-fold): %.3f +/- %.3f",
            n_splits, scores.mean(), scores.std(),
        )

        # Fit on full data with sample weights
        ensemble.fit(X, y, sample_weight=sample_weight)

        self._ensemble = ensemble
        self._trained = True
        self._labels_at_last_train = counts["total"]
        logger.info(
            "Qualification classifier trained on %d samples (%d positive, %d negative), %d estimators",
            counts["total"], counts["positive"], counts["negative"], n_estimators,
        )
        return True

    def predict_distribution(self, embedding: np.ndarray) -> np.ndarray:
        """Return array of probabilities from each estimator (posterior samples)."""
        if not self._trained:
            return np.array([])

        X = embedding.reshape(1, -1)
        probs = np.array([
            est.predict_proba(X)[0, 1]
            for est in self._ensemble.estimators_
        ])
        return probs

    def predict(self, embedding: np.ndarray) -> tuple[float, float]:
        """Return (mean_probability, std_probability) from ensemble."""
        probs = self.predict_distribution(embedding)
        if len(probs) == 0:
            raise RuntimeError("predict() called on untrained scorer")
        return (float(probs.mean()), float(probs.std()))

    def score_profiles(self, profiles: list) -> list:
        """Score and rank profiles for the connect lane.

        Scoring chain:
        1. Seeds (meta.seed=true) always ranked first
        2. If classifier trained → sample a random estimator (natural Thompson Sampling)
        3. If seeds exist but no classifier → cosine similarity + Thompson Sampling
        4. No seeds, no classifier → FIFO
        """
        if not profiles:
            return list(profiles)

        seeds = []
        non_seeds = []
        for p in profiles:
            meta = p.get("meta", {}) or {}
            if meta.get("seed"):
                seeds.append(p)
            else:
                non_seeds.append(p)

        if not non_seeds:
            return seeds

        # Trained classifier: sample a random estimator for Thompson Sampling
        if self._trained:
            estimator_idx = self._rng.randint(0, len(self._ensemble.estimators_))
            estimator = self._ensemble.estimators_[estimator_idx]

            scored = []
            for p in non_seeds:
                embedding = self._get_embedding(p)
                if embedding is not None:
                    X = embedding.reshape(1, -1)
                    prob = estimator.predict_proba(X)[0, 1]
                else:
                    prob = 0.5
                scored.append((prob, p))

            scored.sort(key=lambda x: x[0], reverse=True)
            return seeds + [p for _, p in scored]

        # No classifier but positive centroid exists → cosine similarity + Thompson Sampling
        from linkedin.ml.embeddings import get_positive_centroid

        centroid = get_positive_centroid()
        if centroid is not None:
            scored = []
            for p in non_seeds:
                embedding = self._get_embedding(p)
                if embedding is not None:
                    sim = self._cosine_similarity(embedding, centroid)
                    # Thompson Sampling with k=5
                    prob = max(0.0, min(1.0, (sim + 1) / 2))  # normalize [-1,1] → [0,1]
                    alpha = prob * 5
                    beta_param = (1 - prob) * 5
                    sampled = self._rng.beta(max(alpha, 0.01), max(beta_param, 0.01))
                else:
                    sampled = self._rng.uniform()
                scored.append((sampled, p))

            scored.sort(key=lambda x: x[0], reverse=True)
            return seeds + [p for _, p in scored]

        # FIFO
        return seeds + non_seeds

    def explain_profile(self, profile: dict) -> str:
        """Human-readable explanation of scoring for a profile."""
        if not self._trained:
            return "Classifier not trained — using similarity or FIFO ordering"

        embedding = self._get_embedding(profile)
        if embedding is None:
            return "No embedding found for profile"

        probs = self.predict_distribution(embedding)
        mean_prob = probs.mean()
        std_prob = probs.std()

        lines = [
            f"Mean probability: {mean_prob:.3f}",
            f"Std (uncertainty): {std_prob:.3f}",
            f"Estimator range: [{probs.min():.3f}, {probs.max():.3f}]",
        ]

        # Show a few individual estimator predictions
        sorted_probs = np.sort(probs)
        n = len(sorted_probs)
        lines.append(f"Estimator quartiles: p25={sorted_probs[n//4]:.3f}, "
                      f"p50={sorted_probs[n//2]:.3f}, p75={sorted_probs[3*n//4]:.3f}")

        return "\n".join(lines)

    def needs_retrain(self) -> bool:
        """Check if enough new labels have accumulated since last training."""
        from linkedin.ml.embeddings import count_labeled

        counts = count_labeled()
        new_labels = counts["total"] - self._labels_at_last_train
        return new_labels >= self._cfg["qualification_retrain_every"]

    def _get_embedding(self, profile: dict) -> np.ndarray | None:
        """Look up profile embedding from DuckDB."""
        from linkedin.ml.embeddings import _connect

        public_id = profile.get("public_identifier")
        if not public_id:
            return None

        con = _connect(read_only=True)
        row = con.execute(
            "SELECT embedding FROM profile_embeddings WHERE public_identifier = ?",
            [public_id],
        ).fetchone()
        con.close()

        if row:
            return np.array(row[0], dtype=np.float32)
        return None

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """Compute cosine similarity between two vectors."""
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))
