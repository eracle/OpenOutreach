# linkedin/ml/qualifier.py
"""GP Regression qualifier: BALD active learning via exact GP posterior."""
from __future__ import annotations

import logging
from pathlib import Path

import jinja2
import numpy as np
from pydantic import BaseModel, Field
from scipy.stats import norm

from linkedin.conf import CAMPAIGN_CONFIG, PROMPTS_DIR

logger = logging.getLogger(__name__)


def format_prediction(prob: float, entropy: float, std: float, n_obs: int) -> str:
    """Compact one-liner stats string for qualification logging."""
    return f"P(f>0.5)={prob:.3f}, entropy={entropy:.4f}, std={std:.4f}, obs={n_obs}"


class QualificationDecision(BaseModel):
    """Structured LLM output for lead qualification."""
    qualified: bool = Field(description="True if the profile is a good prospect, False otherwise")
    reason: str = Field(description="Brief explanation for the decision")


def qualify_with_llm(profile_text: str, product_docs: str, campaign_objective: str) -> tuple[int, str]:
    """Call LLM to qualify a profile. Returns (label, reason).

    label: 1 = accept, 0 = reject.
    """
    from langchain_openai import ChatOpenAI

    from linkedin.conf import AI_MODEL, LLM_API_KEY, LLM_API_BASE

    if LLM_API_KEY is None:
        raise ValueError("LLM_API_KEY is not set in the environment or config.")

    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(PROMPTS_DIR)))
    template = env.get_template("qualify_lead.j2")

    prompt = template.render(
        product_docs=product_docs,
        campaign_objective=campaign_objective,
        profile_text=profile_text,
    )

    llm = ChatOpenAI(model=AI_MODEL, temperature=0.7, api_key=LLM_API_KEY, base_url=LLM_API_BASE, timeout=60)
    structured_llm = llm.with_structured_output(QualificationDecision)
    decision = structured_llm.invoke(prompt)

    label = 1 if decision.qualified else 0
    return (label, decision.reason)


# ---------------------------------------------------------------------------
# Numerics
# ---------------------------------------------------------------------------

def _binary_entropy(p):
    """H(p) = -p log p - (1-p) log(1-p), safe for edge values."""
    p = np.asarray(p, dtype=np.float64)
    p = np.clip(p, 1e-12, 1.0 - 1e-12)
    return -p * np.log(p) - (1.0 - p) * np.log(1.0 - p)


def _prob_above_half(mean, std):
    """P(f > 0.5) from GP posterior."""
    return norm.sf(0.5, loc=mean, scale=std)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _gpr_predict(pipe, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Transform through all steps except GPR, then predict with return_std.

    Used by BayesianQualifier for BALD, predict_probs, and predict —
    operations that need the posterior std.  Ranking uses the simpler
    ``pipeline.predict(X)`` (mean only) instead.
    """
    from sklearn.pipeline import Pipeline

    X = np.asarray(X, dtype=np.float64)
    if X.ndim == 1:
        X = X.reshape(1, -1)
    X_transformed = Pipeline(pipe.steps[:-1]).transform(X)
    return pipe.named_steps['gpr'].predict(X_transformed, return_std=True)


def _load_profile_embeddings(profiles: list, session, *, skip_missing: bool = False):
    """Load embeddings for a list of profile dicts.

    Returns list of (profile, embedding) pairs.
    """
    from linkedin.db.enrichment import load_embedding

    result = []
    for p in profiles:
        emb = load_embedding(p.get("lead_id"), p.get("public_identifier"), session)
        if emb is None:
            if skip_missing:
                continue
            pid = p.get("public_identifier", "?")
            raise RuntimeError(f"No embedding found for profile {pid}")
        result.append((p, emb))
    return result


def _rank_by_score(profiles: list, pipeline, session, *, skip_missing: bool = False) -> list:
    """Rank profiles by raw pipeline.predict() score (descending).

    Works with any sklearn-compatible pipeline — no GPR-specific logic.
    """
    scored = _load_profile_embeddings(profiles, session, skip_missing=skip_missing)
    if not scored:
        return []

    X = np.array([emb for _, emb in scored], dtype=np.float64)
    scores = pipeline.predict(X)

    ranked = sorted(zip(scores, [p for p, _ in scored]), key=lambda t: t[0], reverse=True)
    return [p for _, p in ranked]


def _explain_score(pipeline, embedding: np.ndarray) -> float:
    """Return the raw prediction score for a single embedding."""
    X = np.asarray(embedding, dtype=np.float64)
    if X.ndim == 1:
        X = X.reshape(1, -1)
    return float(pipeline.predict(X)[0])


# ---------------------------------------------------------------------------
# BayesianQualifier  (GP Regression backend)
# ---------------------------------------------------------------------------

class BayesianQualifier:
    """Gaussian Process Regressor for active learning qualification.

    Uses an sklearn Pipeline (StandardScaler -> GPR) as a single
    serializable brick.  GPR provides an exact closed-form posterior
    (no Laplace approximation), avoiding the degenerate-0.5 problem
    that plagues GPC on weakly separable embedding data.  Probabilities
    are computed as P(f > 0.5) from the GP posterior, which naturally
    incorporates uncertainty and stays in [0, 1] without clipping.

    BALD scores are computed via MC sampling from the GP posterior
    f ~ N(f_mean, f_std) for candidate selection; predictive entropy
    gates auto-decisions vs LLM queries.

    Training data is accumulated incrementally; the GPR is lazily
    re-fitted on ALL accumulated data whenever predictions are needed.
    """

    def __init__(self, seed: int = 42, embedding_dim: int = 384, n_mc_samples: int = 100,
                 save_path: Path | None = None):
        self.embedding_dim = embedding_dim
        self._seed = seed
        self._n_mc_samples = n_mc_samples
        self._pipeline = None  # Pipeline([('scaler', StandardScaler), ('gpr', GPR)])
        self._save_path = save_path
        self._X: list[np.ndarray] = []
        self._y: list[int] = []
        self._fitted = False
        self._rng = np.random.RandomState(seed)

    @property
    def n_obs(self) -> int:
        return len(self._y)

    @property
    def class_counts(self) -> tuple[int, int]:
        """Return (n_negatives, n_positives)."""
        n_pos = sum(self._y)
        return len(self._y) - n_pos, n_pos

    @property
    def pipeline(self):
        """The fitted sklearn Pipeline — serializable via joblib."""
        self._fit_if_needed()
        return self._pipeline

    # ------------------------------------------------------------------
    # Update  (append + invalidate)
    # ------------------------------------------------------------------

    def update(self, embedding: np.ndarray, label: int):
        """Record a new labelled observation.  Model is lazily re-fitted."""
        self._X.append(embedding.astype(np.float64).ravel())
        self._y.append(int(label))
        self._fitted = False

    # ------------------------------------------------------------------
    # Lazy refit
    # ------------------------------------------------------------------

    # Maximum ratio of majority-to-minority samples for GP fitting.
    # Beyond this, the majority class is subsampled to prevent degenerate
    # predictions when labels are heavily imbalanced.
    _MAX_IMBALANCE_RATIO = 2

    def _fit_if_needed(self) -> bool:
        """Fit StandardScaler + GPR pipeline if dirty and feasible.  Returns True when model is usable."""
        if self._fitted:
            return True
        if len(self._y) < 2:
            return False
        y_arr = np.array(self._y, dtype=np.float64)
        if len(np.unique(y_arr)) < 2:
            return False  # need both classes

        from sklearn.gaussian_process import GaussianProcessRegressor
        from sklearn.gaussian_process.kernels import ConstantKernel, RBF
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler

        X_arr = np.array(self._X, dtype=np.float64)
        X_fit, y_fit = self._balance(X_arr, y_arr)
        n = X_fit.shape[0]

        self._pipeline = Pipeline([
            ('scaler', StandardScaler()),
            ('gpr', GaussianProcessRegressor(
                kernel=ConstantKernel(1.0) * RBF(length_scale=np.sqrt(self.embedding_dim)),
                n_restarts_optimizer=3,
                random_state=self._seed,
                alpha=0.1,
            )),
        ])
        self._pipeline.fit(X_fit, y_fit)
        lml = self._pipeline.named_steps['gpr'].log_marginal_likelihood_value_

        self._fitted = True
        logger.debug("GPR fitted on %d observations (%d after balancing, LML=%.2f)",
                      len(self._y), n, lml)
        self._persist_pipeline()
        return True

    def _balance(self, X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Subsample the majority class to at most _MAX_IMBALANCE_RATIO * minority.

        Prevents the GP from becoming degenerate when one class dominates.
        Keeps all minority samples and selects majority samples randomly.
        """
        n_pos = int(y.sum())
        n_neg = len(y) - n_pos
        n_min = min(n_pos, n_neg)
        n_max = max(n_pos, n_neg)
        cap = self._MAX_IMBALANCE_RATIO * n_min

        if n_max <= cap:
            return X, y  # already balanced enough

        minority_label = 1.0 if n_pos < n_neg else 0.0
        minority_idx = np.where(y == minority_label)[0]
        majority_idx = np.where(y != minority_label)[0]

        chosen = self._rng.choice(majority_idx, size=cap, replace=False)
        keep = np.concatenate([minority_idx, chosen])
        keep.sort()

        logger.debug(
            "Balancing training set: %d → %d (kept all %d minority, "
            "subsampled %d → %d majority)",
            len(y), len(keep), n_min, n_max, cap,
        )
        return X[keep], y[keep]

    def _persist_pipeline(self):
        """Persist the fitted pipeline to disk (if save_path is set)."""
        if self._save_path is None or self._pipeline is None:
            return
        import joblib

        tmp = self._save_path.with_suffix(".tmp")
        joblib.dump(self._pipeline, tmp)
        tmp.rename(self._save_path)
        logger.debug("Pipeline saved to %s", self._save_path)

    # ------------------------------------------------------------------
    # Prediction  (needs posterior std — uses _gpr_predict)
    # ------------------------------------------------------------------

    def predict(self, embedding: np.ndarray) -> tuple[float, float, float] | None:
        """Return (predictive_prob, predictive_entropy, posterior_std) for a single embedding.

        Probability is P(f > 0.5) from the GP posterior, which naturally
        incorporates uncertainty and stays in [0, 1] without clipping.
        Returns None when the model cannot be fitted yet.
        """
        if not self._fit_if_needed():
            return None
        mean, std = _gpr_predict(self._pipeline, embedding)
        p = float(_prob_above_half(mean, std)[0])
        entropy = float(_binary_entropy(p))
        return p, entropy, float(std[0])

    # ------------------------------------------------------------------
    # BALD acquisition via GP posterior
    # ------------------------------------------------------------------

    def compute_bald(self, embeddings: np.ndarray) -> np.ndarray | None:
        """BALD scores for (N, embedding_dim) candidates.

        BALD = H(E[p]) - E[H(p)], computed by MC-sampling from the
        exact GP posterior f ~ N(mean, std) with a probit link
        p = Φ(f - 0.5).  Higher BALD = model disagrees with itself
        most = most informative to query.

        Returns None when the model cannot be fitted yet.
        """
        if not self._fit_if_needed():
            return None

        f_mean, f_std = _gpr_predict(self._pipeline, embeddings)

        # MC sample: (M, N) draws from GP posterior
        f_samples = (
            f_mean[np.newaxis, :]
            + f_std[np.newaxis, :] * self._rng.randn(self._n_mc_samples, len(f_mean))
        )
        # Probit link: each sample gives a smooth probability via Φ(f - 0.5)
        p_samples = norm.cdf(f_samples - 0.5)

        p_pred = p_samples.mean(axis=0)
        H_pred = _binary_entropy(p_pred)
        H_individual = _binary_entropy(p_samples).mean(axis=0)
        return H_pred - H_individual

    # ------------------------------------------------------------------
    # Predicted probabilities (exploitation)
    # ------------------------------------------------------------------

    def predict_probs(self, embeddings: np.ndarray) -> np.ndarray | None:
        """Predicted probability P(f > 0.5) for each candidate.

        Returns None when the model cannot be fitted yet.
        """
        if not self._fit_if_needed():
            return None
        mean, std = _gpr_predict(self._pipeline, embeddings)
        return _prob_above_half(mean, std)

    def acquisition_scores(self, embeddings: np.ndarray) -> tuple[str, np.ndarray] | None:
        """Score candidates using the balance-driven acquisition strategy.

        - Exploit mode (n_neg > n_pos): returns predicted probabilities P(f > 0.5)
        - Explore mode: returns BALD information gain scores

        Returns ``(strategy_name, scores)`` or ``None`` on cold start.
        """
        n_neg, n_pos = self.class_counts
        if n_neg > n_pos:
            scores = self.predict_probs(embeddings)
            strategy = "exploit (p)"
        else:
            scores = self.compute_bald(embeddings)
            strategy = "explore (BALD)"
        if scores is None:
            return None
        return strategy, scores

    def pool_has_targets(self, embeddings: np.ndarray) -> bool | None:
        """Check if the unlabeled pool has any promising candidates (P > 0.5).

        Returns None on cold start (model not fitted), True/False otherwise.
        Only checks for positive-looking profiles — searching for low-P
        profiles (explore mode) would be wasteful since you can just qualify
        from the existing pool.
        """
        probs = self.predict_probs(embeddings)
        if probs is None:
            return None
        return bool(np.any(probs > 0.5))

    # ------------------------------------------------------------------
    # Ranking & explain  (raw GP mean — no _prob_above_half)
    # ------------------------------------------------------------------

    def rank_profiles(self, profiles: list, session) -> list:
        """Rank QUALIFIED profiles by raw GP mean (descending).

        Raises if a profile lacks an embedding after lazy loading.
        """
        if not profiles:
            return []
        if not self._fit_if_needed():
            raise RuntimeError(
                f"GPR not fitted ({self.n_obs} observations) — cannot rank profiles"
            )
        return _rank_by_score(profiles, self._pipeline, session)

    def explain(self, profile: dict, session) -> str:
        """Human-readable compact scoring explanation."""
        from linkedin.db.enrichment import load_embedding

        emb = load_embedding(profile.get("lead_id"), profile.get("public_identifier"), session)
        if emb is None:
            return "No embedding found for profile"
        if not self._fit_if_needed():
            return f"Model not fitted yet ({self.n_obs} observations, need both classes)"
        mean, std = _gpr_predict(self._pipeline, emb)
        gp_mean = float(mean[0])
        p_above = float(_prob_above_half(mean, std)[0])
        return f"mean={gp_mean:.3f}, P(f>0.5)={p_above:.3f}, obs={self.n_obs}"

    # ------------------------------------------------------------------
    # Warm start
    # ------------------------------------------------------------------

    def warm_start(self, X: np.ndarray, y: np.ndarray):
        """Bulk-load historical labels and fit once."""
        self._X = [X[i].astype(np.float64).ravel() for i in range(len(X))]
        self._y = [int(y[i]) for i in range(len(y))]
        self._fitted = False
        if len(self._X) >= 2:
            self._fit_if_needed()


# ---------------------------------------------------------------------------
# KitQualifier  (pre-trained kit model for partner campaigns)
# ---------------------------------------------------------------------------

class KitQualifier:
    """Qualifier for partner campaigns backed by a pre-trained GPR kit model.

    Wraps a Pipeline(StandardScaler, GPR) loaded from a campaign kit.
    Ranks by raw GP mean and exposes posterior stats for explanation.
    """

    def __init__(self, kit_model):
        self._model = kit_model

    def rank_profiles(self, profiles: list, session) -> list:
        """Rank profiles by raw model score (descending), skipping missing embeddings."""
        if not profiles:
            return []
        return _rank_by_score(profiles, self._model, session, skip_missing=True)

    def explain(self, profile: dict, session) -> str:
        """Human-readable compact scoring explanation."""
        from linkedin.db.enrichment import load_embedding

        emb = load_embedding(profile.get("lead_id"), profile.get("public_identifier"), session)
        if emb is None:
            return "No embedding found for profile"
        mean, std = _gpr_predict(self._model, emb)
        gp_mean = float(mean[0])
        p_above = float(_prob_above_half(mean, std)[0])
        return f"mean={gp_mean:.3f}, P(f>0.5)={p_above:.3f}"
