# linkedin/ml/qualifier.py
"""GPC-based qualifier: BALD active learning via GP latent posterior."""
from __future__ import annotations

import logging

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

def _sigmoid(x):
    """Numerically stable sigmoid, works for scalars and arrays."""
    x = np.asarray(x, dtype=np.float64)
    return np.where(x >= 0, 1.0 / (1.0 + np.exp(-x)), np.exp(x) / (1.0 + np.exp(x)))


def _binary_entropy(p):
    """H(p) = -p log p - (1-p) log(1-p), safe for edge values."""
    p = np.asarray(p, dtype=np.float64)
    p = np.clip(p, 1e-12, 1.0 - 1e-12)
    return -p * np.log(p) - (1.0 - p) * np.log(1.0 - p)


# ---------------------------------------------------------------------------
# BayesianQualifier  (GPC backend)
# ---------------------------------------------------------------------------

class BayesianQualifier:
    """Gaussian Process Classifier for active learning qualification.

    Uses sklearn GaussianProcessClassifier with RBF kernel for non-linear
    probabilistic classification of profile embeddings.  BALD (Bayesian
    Active Learning by Disagreement) scores are computed via MC sampling
    from the GP latent posterior for candidate selection; predictive entropy
    gates auto-decisions vs LLM queries.

    Training data is accumulated incrementally; the GPC is lazily re-fitted
    (on ALL accumulated data) whenever predictions are requested after new
    labels arrive.  This is identical in accuracy to refitting after every
    label — "lazy" just avoids redundant fits between prediction calls.
    """

    def __init__(self, seed: int = 42, embedding_dim: int = 384, n_mc_samples: int = 100,
                 pca_variance_threshold: float = 0.95):
        from sklearn.gaussian_process import GaussianProcessClassifier
        from sklearn.gaussian_process.kernels import ConstantKernel, RBF

        self.embedding_dim = embedding_dim
        self._seed = seed
        self._n_mc_samples = n_mc_samples
        self._pca_variance_threshold = pca_variance_threshold
        self._base_kernel = ConstantKernel(1.0) * RBF(length_scale=1.0)
        self._gpc = GaussianProcessClassifier(
            kernel=self._base_kernel,
            n_restarts_optimizer=0,
            random_state=seed,
        )
        self._pca = None  # fitted in _ensure_fitted()
        self._scaler = None  # fitted in _ensure_fitted()
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

    def _ensure_fitted(self) -> bool:
        """Fit PCA + GPC if dirty and feasible.  Returns True when model is usable."""
        if self._fitted:
            return True
        if len(self._y) < 2:
            return False
        y_arr = np.array(self._y)
        if len(np.unique(y_arr)) < 2:
            return False  # GPC needs both classes

        from sklearn.decomposition import PCA
        from sklearn.gaussian_process import GaussianProcessClassifier
        from sklearn.preprocessing import StandardScaler

        X_arr = np.array(self._X, dtype=np.float64)

        # Fit PCA: keep enough components to explain pca_variance_threshold of variance
        max_components = min(X_arr.shape[0], X_arr.shape[1])
        self._pca = PCA(n_components=min(self._pca_variance_threshold, max_components),
                        random_state=self._seed)
        X_reduced = self._pca.fit_transform(X_arr)

        # Normalize so length_scale=1.0 is a reasonable starting point
        self._scaler = StandardScaler()
        X_scaled = self._scaler.fit_transform(X_reduced)

        # Reuse previously-fitted kernel params as starting point
        kernel = (
            self._gpc.kernel_
            if hasattr(self._gpc, "kernel_") and self._gpc.kernel_ is not None
            else self._base_kernel
        )
        self._gpc = GaussianProcessClassifier(
            kernel=kernel,
            n_restarts_optimizer=0,
            random_state=self._seed,
        )
        self._gpc.fit(X_scaled, y_arr)
        self._fitted = True
        logger.debug("GPC fitted on %d observations (%d PCA dims, %.1f%% variance)",
                     len(y_arr), self._pca.n_components_,
                     100 * self._pca.explained_variance_ratio_.sum())
        return True

    def _transform(self, X: np.ndarray) -> np.ndarray:
        """Apply fitted PCA + scaler to input(s). Handles both 1D and 2D arrays."""
        X = np.asarray(X, dtype=np.float64)
        if X.ndim == 1:
            X = X.reshape(1, -1)
        return self._scaler.transform(self._pca.transform(X))

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    def predict(self, embedding: np.ndarray) -> tuple[float, float] | None:
        """Return (predictive_prob, predictive_entropy) for a single embedding.

        Returns None when the model cannot be fitted yet (cold start:
        fewer than 2 labels or only one class present).
        """
        if not self._ensure_fitted():
            return None

        x = self._transform(embedding)
        proba = self._gpc.predict_proba(x)[0]
        p = float(proba[1])
        entropy = float(_binary_entropy(p))
        return p, entropy

    # ------------------------------------------------------------------
    # BALD acquisition via GP latent posterior
    # ------------------------------------------------------------------

    def _latent_f(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Extract GP posterior mean and variance of latent f(x) at test points.

        Uses the fitted GPC's internal Laplace approximation attributes.
        X should already be PCA-transformed.
        Returns (f_mean, f_var) each of shape (N,).
        """
        from scipy.linalg import solve_triangular

        base = self._gpc.base_estimator_
        K_star = base.kernel_(X, base.X_train_)
        f_mean = K_star.dot(base.y_train_ - base.pi_)
        v = solve_triangular(base.L_, base.W_sr_[:, np.newaxis] * K_star.T, lower=True)
        f_var = base.kernel_.diag(X) - np.einsum("ij,ij->j", v, v)
        f_var = np.maximum(f_var, 1e-10)
        return f_mean, f_var

    def bald_scores(self, embeddings: np.ndarray) -> np.ndarray | None:
        """BALD scores for (N, embedding_dim) candidates.

        BALD = H(E[p]) - E[H(p)], computed by MC-sampling from the GP
        latent posterior f ~ N(f_mean, f_var) and pushing through sigmoid.
        Higher BALD = model disagrees with itself most = most informative.

        Returns None when the model cannot be fitted yet (caller should
        fall back to FIFO ordering).
        """
        if not self._ensure_fitted():
            return None

        X = self._transform(embeddings)
        f_mean, f_var = self._latent_f(X)

        # MC sample: (M, N) latent function draws
        f_samples = (
            f_mean[np.newaxis, :]
            + np.sqrt(f_var)[np.newaxis, :] * self._rng.randn(self._n_mc_samples, len(X))
        )
        p_samples = _sigmoid(f_samples)  # (M, N)

        p_pred = p_samples.mean(axis=0)  # (N,)
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
        X = self._transform(embeddings)
        return self._gpc.predict_proba(X)[:, 1]

    # ------------------------------------------------------------------
    # Ranking for connect lane
    # ------------------------------------------------------------------

    def rank_profiles(self, profiles: list) -> list:
        """Rank QUALIFIED profiles by predicted acceptance probability (descending)."""
        if not profiles:
            return []

        fitted = self._ensure_fitted()
        scored = []
        for p in profiles:
            emb = self._get_embedding(p)
            if emb is not None and fitted:
                x = self._transform(emb)
                proba = self._gpc.predict_proba(x)[0]
                prob = float(proba[1])
            else:
                prob = 0.5
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
        prob, entropy = result
        return (
            f"GP predictive p(qualified): {prob:.3f}\n"
            f"Predictive entropy: {entropy:.4f}\n"
            f"Observations seen: {self.n_obs}"
        )

    # ------------------------------------------------------------------
    # Warm start
    # ------------------------------------------------------------------

    def warm_start(self, X: np.ndarray, y: np.ndarray):
        """Bulk-load historical labels and fit.

        Unlike the previous linear model's sequential replay, GPC fits
        all data at once — so warm_start is faster than incremental updates.
        """
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

def rank_with_external_model(gpc, pca, profiles: list) -> list:
    """Rank profiles by pre-trained model. Profiles without embeddings are skipped."""
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
    X_reduced = pca.transform(X)
    probs = gpc.predict_proba(X_reduced)[:, 1]

    ranked = sorted(zip(probs, [p for p, _ in scored]), key=lambda t: t[0], reverse=True)
    return [p for _, p in ranked]
