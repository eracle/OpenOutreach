# linkedin/ml/qualifier.py
"""GP Regression qualifier: BALD active learning via exact GP posterior."""
from __future__ import annotations

import logging
from pathlib import Path

import jinja2
import numpy as np
from pydantic import BaseModel, Field

from linkedin.conf import CAMPAIGN_CONFIG, PROMPTS_DIR

logger = logging.getLogger(__name__)


class QualificationDecision(BaseModel):
    """Structured LLM output for lead qualification."""
    qualified: bool = Field(description="True if the profile is a good prospect, False otherwise")
    reason: str = Field(description="Brief explanation for the decision")


def qualify_profile_llm(profile_text: str, product_docs: str, campaign_objective: str) -> tuple[int, str]:
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


# ---------------------------------------------------------------------------
# BayesianQualifier  (GP Regression backend)
# ---------------------------------------------------------------------------

class BayesianQualifier:
    """Gaussian Process Regressor for active learning qualification.

    Uses an sklearn Pipeline (PCA -> StandardScaler -> GPR) as a single
    serializable brick.  GPR provides an exact closed-form posterior
    (no Laplace approximation), avoiding the degenerate-0.5 problem
    that plagues GPC on weakly separable embedding data.  Predictions
    are clipped to [0, 1] for probability interpretation.

    BALD scores are computed via MC sampling from the GP posterior
    f ~ N(f_mean, f_std) for candidate selection; predictive entropy
    gates auto-decisions vs LLM queries.

    PCA dimensionality is selected via leave-one-out cross-validation
    (GPR provides analytical LOO log-likelihood) on each refit.

    Training data is accumulated incrementally; the GPR is lazily
    re-fitted on ALL accumulated data whenever predictions are needed.
    """

    def __init__(self, seed: int = 42, embedding_dim: int = 384, n_mc_samples: int = 100,
                 save_path: Path | None = None):
        self.embedding_dim = embedding_dim
        self._seed = seed
        self._n_mc_samples = n_mc_samples
        self._pipeline = None  # Pipeline([('pca', PCA), ('scaler', StandardScaler), ('gpr', GPR)])
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
        self._ensure_fitted()
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
    # Lazy refit with PCA CV
    # ------------------------------------------------------------------

    def _ensure_fitted(self) -> bool:
        """Fit PCA + StandardScaler + GPR pipeline if dirty and feasible.  Returns True when model is usable."""
        if self._fitted:
            return True
        if len(self._y) < 2:
            return False
        y_arr = np.array(self._y, dtype=np.float64)
        if len(np.unique(y_arr)) < 2:
            return False  # need both classes

        from sklearn.decomposition import PCA
        from sklearn.gaussian_process import GaussianProcessRegressor
        from sklearn.gaussian_process.kernels import ConstantKernel, RBF
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler

        X_arr = np.array(self._X, dtype=np.float64)
        n = X_arr.shape[0]

        # Select PCA dims via GPR log-marginal-likelihood (analytical LOO proxy)
        max_dims = min(n - 1, X_arr.shape[1])
        candidates = sorted({d for d in [2, 4, 6, 10, 15, 20] if d <= max_dims})
        if not candidates:
            candidates = [max_dims]

        best_lml = -np.inf
        best_pipeline = None

        for n_pca in candidates:
            pipe = Pipeline([
                ('pca', PCA(n_components=n_pca, random_state=self._seed)),
                ('scaler', StandardScaler()),
                ('gpr', GaussianProcessRegressor(
                    kernel=ConstantKernel(1.0) * RBF(length_scale=np.sqrt(n_pca)),
                    n_restarts_optimizer=3,
                    random_state=self._seed,
                    alpha=0.1,
                )),
            ])
            pipe.fit(X_arr, y_arr)
            lml = pipe.named_steps['gpr'].log_marginal_likelihood_value_
            if lml > best_lml:
                best_lml = lml
                best_pipeline = pipe

        self._pipeline = best_pipeline
        self._fitted = True
        pca_step = self._pipeline.named_steps['pca']
        logger.debug("GPR fitted on %d observations (%d PCA dims, %.1f%% variance, LML=%.2f)",
                     n, pca_step.n_components_,
                     100 * pca_step.explained_variance_ratio_.sum(),
                     best_lml)
        self._save()
        return True

    def _save(self):
        """Persist the fitted pipeline to disk (if save_path is set)."""
        if self._save_path is None or self._pipeline is None:
            return
        import joblib

        tmp = self._save_path.with_suffix(".tmp")
        joblib.dump(self._pipeline, tmp)
        tmp.rename(self._save_path)
        logger.debug("Pipeline saved to %s", self._save_path)

    # ------------------------------------------------------------------
    # Internal: predict with std via pipeline
    # ------------------------------------------------------------------

    def _predict_with_std(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Transform through PCA+scaler, then GPR predict with return_std.

        Pipeline.predict doesn't forward return_std, so we split
        transform (steps[:-1]) from predict (last step).
        """
        from sklearn.pipeline import Pipeline

        X = np.asarray(X, dtype=np.float64)
        if X.ndim == 1:
            X = X.reshape(1, -1)
        X_transformed = Pipeline(self._pipeline.steps[:-1]).transform(X)
        return self._pipeline.named_steps['gpr'].predict(X_transformed, return_std=True)

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    def predict(self, embedding: np.ndarray) -> tuple[float, float, float] | None:
        """Return (predictive_prob, predictive_entropy, posterior_std) for a single embedding.

        Probability is GPR mean clipped to [0, 1].
        posterior_std is the GP's uncertainty about the function value — high
        when few training points are nearby (e.g. early training).
        Returns None when the model cannot be fitted yet.
        """
        if not self._ensure_fitted():
            return None

        mean, std = self._predict_with_std(embedding)
        p = float(np.clip(mean[0], 0.0, 1.0))
        entropy = float(_binary_entropy(p))
        return p, entropy, float(std[0])

    # ------------------------------------------------------------------
    # BALD acquisition via GP posterior
    # ------------------------------------------------------------------

    def bald_scores(self, embeddings: np.ndarray) -> np.ndarray | None:
        """BALD scores for (N, embedding_dim) candidates.

        BALD = H(E[p]) - E[H(p)], computed by MC-sampling from the
        exact GP posterior f ~ N(mean, std) and clipping to [0, 1].
        Higher BALD = model disagrees with itself most = most informative.

        Returns None when the model cannot be fitted yet.
        """
        if not self._ensure_fitted():
            return None

        f_mean, f_std = self._predict_with_std(embeddings)

        # MC sample: (M, N) draws from GP posterior
        f_samples = (
            f_mean[np.newaxis, :]
            + f_std[np.newaxis, :] * self._rng.randn(self._n_mc_samples, len(f_mean))
        )
        p_samples = np.clip(f_samples, 0.0, 1.0)

        p_pred = p_samples.mean(axis=0)
        H_pred = _binary_entropy(p_pred)
        H_individual = _binary_entropy(p_samples).mean(axis=0)
        return H_pred - H_individual

    # ------------------------------------------------------------------
    # Predicted probabilities (exploitation)
    # ------------------------------------------------------------------

    def predicted_probs(self, embeddings: np.ndarray) -> np.ndarray | None:
        """Predicted probability p(qualified) for each candidate.

        Returns None when the model cannot be fitted yet.
        """
        if not self._ensure_fitted():
            return None
        X = np.asarray(embeddings, dtype=np.float64)
        if X.ndim == 1:
            X = X.reshape(1, -1)
        mean = self._pipeline.predict(X)
        return np.clip(mean, 0.0, 1.0)

    # ------------------------------------------------------------------
    # Ranking for connect lane
    # ------------------------------------------------------------------

    def rank_profiles(self, profiles: list) -> list:
        """Rank QUALIFIED profiles by predicted acceptance probability (descending).

        Raises if the model is not fitted or any profile lacks an embedding.
        """
        if not profiles:
            return []

        if not self._ensure_fitted():
            raise RuntimeError(
                f"GPR not fitted ({self.n_obs} observations) — cannot rank profiles"
            )

        scored = []
        for p in profiles:
            emb = self._get_embedding(p)
            if emb is None:
                pid = p.get("public_identifier", "?")
                raise RuntimeError(f"No embedding found for profile {pid}")
            x = np.asarray(emb, dtype=np.float64).reshape(1, -1)
            prob = float(np.clip(self._pipeline.predict(x)[0], 0.0, 1.0))
            scored.append((prob, p))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [p for _, p in scored]

    # ------------------------------------------------------------------
    # Explain
    # ------------------------------------------------------------------

    def explain_profile(self, profile: dict) -> str:
        """Human-readable scoring explanation."""
        emb = self._get_embedding(profile)
        if emb is None:
            return "No embedding found for profile"
        result = self.predict(emb)
        if result is None:
            return f"Model not fitted yet ({self.n_obs} observations, need both classes)"
        prob, entropy, std = result
        return (
            f"GP predictive p(qualified): {prob:.3f}\n"
            f"Predictive entropy: {entropy:.4f}\n"
            f"Posterior std: {std:.4f}\n"
            f"Observations seen: {self.n_obs}"
        )

    # ------------------------------------------------------------------
    # Warm start
    # ------------------------------------------------------------------

    def warm_start(self, X: np.ndarray, y: np.ndarray):
        """Bulk-load historical labels and fit once."""
        self._X = [X[i].astype(np.float64).ravel() for i in range(len(X))]
        self._y = [int(y[i]) for i in range(len(y))]
        self._fitted = False
        if len(self._X) >= 2:
            self._ensure_fitted()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# External model ranking (partner campaign)
# ---------------------------------------------------------------------------

def rank_with_external_model(pipeline, profiles: list) -> list:
    """Rank profiles by pre-trained pipeline. Profiles without embeddings are skipped."""
    from linkedin.ml.embeddings import _connect

    if not profiles:
        return []

    # Look up embeddings from DuckDB
    scored = []
    con = _connect(read_only=True)
    for p in profiles:
        public_id = p.get("public_identifier")
        if not public_id:
            continue
        row = con.execute(
            "SELECT embedding FROM profile_embeddings WHERE public_identifier = ?",
            [public_id],
        ).fetchone()
        if row is None:
            continue
        scored.append((p, np.array(row[0], dtype=np.float64)))
    con.close()

    if not scored:
        return []

    X = np.array([emb for _, emb in scored], dtype=np.float64)
    probs = np.clip(pipeline.predict(X), 0.0, 1.0)

    ranked = sorted(zip(probs, [p for p, _ in scored]), key=lambda t: t[0], reverse=True)
    return [p for _, p in ranked]
