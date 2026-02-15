# linkedin/ml/qualifier.py
"""BayesianQualifier: online Bayesian logistic regression with BALD acquisition."""
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
# BayesianQualifier
# ---------------------------------------------------------------------------

class BayesianQualifier:
    """Online Bayesian Logistic Regression with Laplace approximation.

    Maintains a Gaussian posterior N(mu, Sigma) over weights w in R^d
    where d = embedding_dim + 1 (bias).  Updated incrementally on each
    new label via rank-1 Sherman-Morrison.

    Acquisition: BALD (Bayesian Active Learning by Disagreement).
    Gating: predictive entropy for auto-decide vs LLM query.
    """

    def __init__(
        self,
        prior_precision: float = 1.0,
        n_mc_samples: int = 100,
        seed: int = 42,
        embedding_dim: int = 384,
    ):
        self.d = embedding_dim + 1  # +1 bias
        self.prior_precision = prior_precision
        self.n_mc_samples = n_mc_samples
        self.mu = np.zeros(self.d, dtype=np.float64)
        self.Sigma = np.eye(self.d, dtype=np.float64) / prior_precision
        self.n_obs = 0
        self._rng = np.random.RandomState(seed)

    # ------------------------------------------------------------------
    # Online posterior update  (O(d^2) per observation)
    # ------------------------------------------------------------------

    def update(self, embedding: np.ndarray, label: int):
        """Rank-1 Sherman-Morrison update of the Gaussian posterior."""
        x = np.append(embedding.astype(np.float64), 1.0)  # bias
        p = float(_sigmoid(self.mu @ x))
        lam = p * (1.0 - p) + 1e-12  # Hessian of log-likelihood
        Sx = self.Sigma @ x
        xSx = float(x @ Sx)
        denom = 1.0 + lam * xSx
        self.Sigma -= lam * np.outer(Sx, Sx) / denom
        self.mu += (label - p) * Sx / denom
        self.n_obs += 1

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    def predict(self, embedding: np.ndarray) -> tuple[float, float]:
        """Return (predictive_prob, bald_score) for a single embedding."""
        x = np.append(embedding.astype(np.float64), 1.0).reshape(1, -1)
        probs = self._mc_probs(x)  # (M, 1)
        col = probs[:, 0]
        p_pred = float(col.mean())
        bald = float(_binary_entropy(p_pred) - _binary_entropy(col).mean())
        return p_pred, bald

    # ------------------------------------------------------------------
    # BALD acquisition  (vectorised over N candidates)
    # ------------------------------------------------------------------

    def bald_scores(self, embeddings: np.ndarray) -> np.ndarray:
        """BALD scores for (N, embedding_dim) matrix. Returns shape (N,)."""
        X = np.hstack([
            embeddings.astype(np.float64),
            np.ones((len(embeddings), 1), dtype=np.float64),
        ])  # (N, d)
        probs = self._mc_probs(X)  # (M, N)
        p_pred = probs.mean(axis=0)  # (N,)
        H_pred = _binary_entropy(p_pred)
        H_individual = _binary_entropy(probs).mean(axis=0)
        return H_pred - H_individual

    # ------------------------------------------------------------------
    # Ranking for connect lane
    # ------------------------------------------------------------------

    def rank_profiles(self, profiles: list) -> list:
        """Rank QUALIFIED profiles by posterior predictive probability (descending)."""
        if not profiles:
            return []

        scored = []
        for p in profiles:
            emb = self._get_embedding(p)
            if emb is not None:
                x = np.append(emb.astype(np.float64), 1.0)
                prob = float(_sigmoid(self.mu @ x))
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
        prob, bald = self.predict(emb)
        entropy = float(_binary_entropy(prob))
        return (
            f"Posterior predictive p(qualified): {prob:.3f}\n"
            f"Predictive entropy: {entropy:.4f}\n"
            f"BALD score: {bald:.4f}\n"
            f"Observations seen: {self.n_obs}"
        )

    # ------------------------------------------------------------------
    # Warm start
    # ------------------------------------------------------------------

    def warm_start(self, X: np.ndarray, y: np.ndarray):
        """Replay historical labelled data to restore posterior on restart."""
        for i in range(len(X)):
            self.update(X[i], int(y[i]))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _mc_probs(self, X: np.ndarray) -> np.ndarray:
        """Draw M posterior weight samples, return sigmoid probs.

        X : (N, d)
        Returns : (M, N)
        """
        jitter = 1e-6 * np.eye(self.d, dtype=np.float64)
        try:
            L = np.linalg.cholesky(self.Sigma + jitter)
        except np.linalg.LinAlgError:
            # Eigenvalue clamp fallback
            eigvals, eigvecs = np.linalg.eigh(self.Sigma)
            eigvals = np.maximum(eigvals, 1e-8)
            L = eigvecs * np.sqrt(eigvals)

        Z = self._rng.randn(self.n_mc_samples, self.d)
        W = self.mu + Z @ L.T  # (M, d)
        logits = W @ X.T  # (M, N)
        return _sigmoid(logits)

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
