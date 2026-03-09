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
    """Compact one-liner stats string."""
    return f"prob={prob:.3f}, entropy={entropy:.4f}, std={std:.4f}, obs={n_obs}"


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
# BayesianQualifier  (GP Regression backend)
# ---------------------------------------------------------------------------

class BayesianQualifier:
    """Gaussian Process Regressor for active learning qualification.

    Uses an sklearn Pipeline (PCA -> StandardScaler -> GPR) as a single
    serializable brick.  GPR provides an exact closed-form posterior
    (no Laplace approximation), avoiding the degenerate-0.5 problem
    that plagues GPC on weakly separable embedding data.  Probabilities
    are computed as P(f > 0.5) from the GP posterior, which naturally
    incorporates uncertainty and stays in [0, 1] without clipping.

    BALD scores are computed via MC sampling from the GP posterior
    f ~ N(f_mean, f_std) for candidate selection; predictive entropy
    gates auto-decisions vs LLM queries.

    PCA dimensionality is selected via leave-one-out cross-validation
    (GPR provides analytical LOO log-likelihood) on each refit.

    Training data is accumulated incrementally; the GPR is lazily
    re-fitted on ALL accumulated data whenever predictions are needed.
    """

    def __init__(self, seed: int = 42, embedding_dim: int = 384, n_mc_samples: int = 100,
                 save_path: Path | None = None, calibration_strength: float = 0.0):
        self.embedding_dim = embedding_dim
        self._seed = seed
        self._n_mc_samples = n_mc_samples
        self._calibration_strength = calibration_strength
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
    # Lazy refit with PCA CV
    # ------------------------------------------------------------------

    def _fit_if_needed(self) -> bool:
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
        self._persist_pipeline()
        return True

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
    # Temperature-scaled calibration
    # ------------------------------------------------------------------

    def _calibrate_probs(self, probs: np.ndarray) -> np.ndarray:
        """Temperature scaling: push probabilities toward 0.5 when data is sparse.

        T = 1 + c / sqrt(n_obs).  With few labels T >> 1 compresses
        probabilities toward 0.5; as labels grow T → 1 and raw GP
        probabilities pass through unchanged.  This is Platt-style
        post-hoc calibration applied to the GP's P(f > 0.5).
        """
        if self._calibration_strength <= 0:
            return probs
        from scipy.special import expit, logit as sp_logit

        T = 1.0 + self._calibration_strength / max(self.n_obs, 1) ** 0.5
        probs = np.clip(probs, 1e-12, 1.0 - 1e-12)
        return expit(sp_logit(probs) / T)

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    def predict(self, embedding: np.ndarray) -> tuple[float, float, float] | None:
        """Return (predictive_prob, predictive_entropy, posterior_std) for a single embedding.

        Probability is P(f > 0.5) from the GP posterior, which naturally
        incorporates uncertainty and stays in [0, 1] without clipping.
        Returns None when the model cannot be fitted yet.
        """
        if not self._fit_if_needed():
            return None

        mean, std = self._gpr_predict(self._pipeline, embedding)
        raw_p = _prob_above_half(mean, std)
        p = float(self._calibrate_probs(raw_p)[0])
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

        f_mean, f_std = self._gpr_predict(self._pipeline,embeddings)

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
        mean, std = self._gpr_predict(self._pipeline, embeddings)
        return self._calibrate_probs(_prob_above_half(mean, std))

    # ------------------------------------------------------------------
    # Ranking for connect lane
    # ------------------------------------------------------------------

    def rank_profiles(self, profiles: list, session, pipeline=None) -> list:
        """Rank QUALIFIED profiles by P(f > 0.5) probability (descending).

        If *pipeline* is provided (partner campaign model), use it instead
        of the internal model.  Both paths extract mean+std from the GPR
        step to compute proper posterior probabilities.
        Raises if a non-partner profile lacks an embedding after lazy loading.
        """
        from linkedin.db.crm_profiles import load_embedding

        if not profiles:
            return []

        pipe = self._get_pipeline(pipeline)

        scored = []
        for p in profiles:
            emb = load_embedding(p.get("lead_id"), p.get("public_identifier"), session)
            if emb is None:
                if pipeline is not None:
                    continue  # partner: skip missing embeddings
                pid = p.get("public_identifier", "?")
                raise RuntimeError(f"No embedding found for profile {pid}")
            scored.append((p, emb))

        if not scored:
            return []

        X = np.array([emb for _, emb in scored], dtype=np.float64)
        mean, std = self._gpr_predict(pipe, X)
        probs = self._calibrate_probs(_prob_above_half(mean, std))

        ranked = sorted(zip(probs, [p for p, _ in scored]), key=lambda t: t[0], reverse=True)
        return [p for _, p in ranked]

    def _get_pipeline(self, external_pipeline=None):
        """Return the pipeline to use for predictions — external or internal."""
        if external_pipeline is not None:
            return external_pipeline
        if not self._fit_if_needed():
            raise RuntimeError(
                f"GPR not fitted ({self.n_obs} observations) — cannot rank profiles"
            )
        return self._pipeline

    @staticmethod
    def _gpr_predict(pipe, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Transform through all steps except GPR, then predict with return_std."""
        from sklearn.pipeline import Pipeline

        X = np.asarray(X, dtype=np.float64)
        if X.ndim == 1:
            X = X.reshape(1, -1)
        X_transformed = Pipeline(pipe.steps[:-1]).transform(X)
        return pipe.named_steps['gpr'].predict(X_transformed, return_std=True)

    # ------------------------------------------------------------------
    # Explain
    # ------------------------------------------------------------------

    def explain(self, profile: dict, session) -> str:
        """Human-readable compact scoring explanation."""
        from linkedin.db.crm_profiles import load_embedding

        emb = load_embedding(profile.get("lead_id"), profile.get("public_identifier"), session)
        if emb is None:
            return "No embedding found for profile"
        result = self.predict(emb)
        if result is None:
            return f"Model not fitted yet ({self.n_obs} observations, need both classes)"
        prob, entropy, std = result
        return format_prediction(prob, entropy, std, self.n_obs)

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

