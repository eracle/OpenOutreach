# openoutreach/core/ml/qualifier.py
"""GP Regression qualifier: BALD active learning via exact GP posterior."""
from __future__ import annotations

import logging
import time
from typing import Protocol, runtime_checkable

import jinja2
import numpy as np
from pydantic import BaseModel, Field
from scipy.stats import norm

from openoutreach.core.conf import CAMPAIGN_CONFIG, PROMPTS_DIR

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Qualifier protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class Qualifier(Protocol):
    """Common interface for all qualifier implementations.

    ``rank_profiles`` returns profiles sorted by score (descending).
    Returns ``[]`` on cold start or when ranking is impossible.

    ``explain`` returns a human-readable scoring summary for a single profile.

    ``predict_probs`` returns P(f > 0.5) per embedding, or ``None`` when the model
    cannot score yet. It belongs here rather than on one implementation because the
    cycle's promote gate (``ready_pool.promote_to_ready``) runs for **every**
    campaign — leaving it off the protocol once let a campaign reach the gate with a
    qualifier that had no such method, and `AttributeError` every other cycle.

    There is only one implementation today (``BayesianQualifier``); the protocol is
    kept because the gate should keep depending on the interface rather than on it.
    """

    def rank_profiles(self, profiles: list) -> list: ...
    def explain(self, profile: dict) -> str: ...
    def predict_probs(self, embeddings: np.ndarray) -> np.ndarray | None: ...


def format_prediction(prob: float, entropy: float, std: float, n_obs: int) -> str:
    """Compact one-liner stats string for qualification logging."""
    return f"P(f>0.5)={prob:.3f}, entropy={entropy:.4f}, std={std:.4f}, obs={n_obs}"


class QualificationDecision(BaseModel):
    """Structured LLM output for lead qualification."""
    qualified: bool = Field(description="True if the profile is a good prospect, False otherwise")
    reason: str = Field(description="Brief explanation for the decision")


def qualify_with_llm(profile_text: str, product_docs: str, campaign_target: str) -> tuple[int, str]:
    """Call LLM to qualify a profile. Returns (label, reason).

    label: 1 = accept, 0 = reject.
    """
    from pydantic_ai import Agent

    from openoutreach.core.llm import get_llm_model, run_agent_sync

    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(PROMPTS_DIR)))
    template = env.get_template("qualify_lead.j2")

    prompt = template.render(
        product_docs=product_docs,
        campaign_target=campaign_target,
        profile_text=profile_text,
    )

    agent = Agent(
        get_llm_model(),
        output_type=QualificationDecision,
        model_settings={"temperature": 0.7, "timeout": 60},
    )
    decision = run_agent_sync(agent.run(prompt)).output

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


def _load_profile_embeddings(profiles: list, *, skip_missing: bool = False):
    """Load cached embeddings for a list of profile dicts.

    Returns list of (profile, embedding) pairs. Reads the cached
    ``Lead.embedding_array`` only — no scrape, so an unembedded lead is missing.
    """
    from openoutreach.crm.models import Lead

    result = []
    for p in profiles:
        lead = Lead.objects.filter(pk=p.get("lead_id")).first()
        emb = lead.embedding_array if lead else None
        if emb is None:
            if skip_missing:
                continue
            pid = p.get("profile_url", "?")
            raise RuntimeError(f"No embedding found for profile {pid}")
        result.append((p, emb))
    return result


def _rank_by_score(profiles: list, pipeline, *, skip_missing: bool = False) -> list:
    """Rank profiles by raw pipeline.predict() score (descending).

    Works with any sklearn-compatible pipeline — no GPR-specific logic.
    """
    scored = _load_profile_embeddings(profiles, skip_missing=skip_missing)
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
                 campaign=None):
        self.embedding_dim = embedding_dim
        self._seed = seed
        self._n_mc_samples = n_mc_samples
        self._pipeline = None  # Pipeline([('scaler', StandardScaler), ('gpr', GPR)])
        self._campaign = campaign
        self._X: list[np.ndarray] = []
        self._y: list[int] = []
        # Synthetic ideal-lead embeddings, all label 1 — kept apart from the real
        # observations, and permanent: they are never trimmed as real positives
        # arrive. See ``set_anchors``.
        self._anchor_X: list[np.ndarray] = []
        self._fitted = False
        self._rng = np.random.RandomState(seed)

    @property
    def n_obs(self) -> int:
        return len(self._y) + len(self._anchor_X)

    @property
    def class_counts(self) -> tuple[int, int]:
        """Return (n_negatives, n_positives) — anchors counted as positives.

        The anchors are permanent, so they always contribute to the positive count,
        not just while the cold phase lasts.
        """
        n_pos = sum(self._y) + len(self._anchor_X)
        return len(self._y) - sum(self._y), n_pos

    @property
    def n_anchors(self) -> int:
        """How many invented positives are standing — always ``ANCHOR_COUNT`` once set."""
        return len(self._anchor_X)

    @property
    def n_real_positives(self) -> int:
        """How many real leads have qualified."""
        return sum(self._y)

    @property
    def has_real_positive(self) -> bool:
        """Whether a real lead has ever qualified.

        Not the phase test (that is ``is_cold``): the anchors stand regardless, so this
        only tells the caller whether ground truth exists alongside them.
        """
        return any(self._y)

    @property
    def is_cold(self) -> bool:
        """Whether the positive class is still mostly invented — the engine's phase test.

        The anchors themselves are permanent (``set_anchors`` never trims them), but the
        phase clock is independent of that: it is ``n_real_positives < ANCHOR_COUNT``, the
        same threshold the anchors were sized to. Once real acceptances reach that count,
        balancing (``_balance``) and the explore/exploit split (``top_up._advance``) take
        over even though the anchors keep contributing to every fit after that — there is
        simply enough real evidence for the balance to be meaningful around them.
        """
        from openoutreach.core.pipeline.icp import ANCHOR_COUNT

        if not self._anchor_X:
            return False
        return self.n_real_positives < ANCHOR_COUNT

    @property
    def pipeline(self):
        """The fitted sklearn Pipeline — serializable via joblib."""
        self._fit_if_needed()
        return self._pipeline

    # ------------------------------------------------------------------
    # Update  (append + invalidate)
    # ------------------------------------------------------------------

    def update(self, embedding: np.ndarray, label: int):
        """Record a new labelled observation.  Model is lazily re-fitted.

        The anchors are never touched here — a positive label adds to the real
        positive class alongside them, it does not displace any of the invented ones.
        """
        self._X.append(embedding.astype(np.float64).ravel())
        self._y.append(int(label))
        self._fitted = False

    # ------------------------------------------------------------------
    # Anchors  (synthetic positives for the cold phase)
    # ------------------------------------------------------------------

    def set_anchors(self, embeddings: np.ndarray):
        """Set the synthetic positives so the GP can fit before any real lead qualifies.

        Without them a first run is unfittable, not merely uninformed: every verdict is
        a rejection until the ICP is right, one class yields no posterior, and BALD,
        P(f>0.5), the promote gate and the query selector all go dark together for the
        whole cold phase. One imagined positive region restores every one of them.

        **Replaces** the anchor set rather than adding to it — the caller owns the whole
        set (``icp.ensure_anchors`` returns every profile written so far), so passing the
        stored set again on a daemon boot is a no-op. They are never trimmed afterwards:
        the anchors stand for the campaign's whole life, alongside whatever real
        positives arrive.
        """
        self._anchor_X = [np.asarray(e, dtype=np.float64).ravel() for e in embeddings]
        self._fitted = False

    def _training_arrays(self) -> tuple[np.ndarray, np.ndarray]:
        """Real observations plus any anchors, as ``(X, y)`` — what the GP fits on."""
        X = self._X + self._anchor_X
        y = self._y + [1] * len(self._anchor_X)
        return np.array(X, dtype=np.float64), np.array(y, dtype=np.float64)

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
        X_arr, y_arr = self._training_arrays()
        if len(y_arr) < 2:
            return False
        if len(np.unique(y_arr)) < 2:
            return False  # need both classes

        from sklearn.gaussian_process import GaussianProcessRegressor
        from sklearn.gaussian_process.kernels import ConstantKernel, RBF
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler

        # Balancing guards against one *observed* class swamping the other, and is skipped
        # during the cold phase: subsampling would throw away real rejections to match a
        # positive class still mostly invented. It takes over once real acceptances reach
        # ANCHOR_COUNT — the anchors keep contributing after that, but there is now enough
        # real evidence for the balance to mean something.
        X_fit, y_fit = (X_arr, y_arr) if self.is_cold else self._balance(X_arr, y_arr)
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
        # Announced *before* it runs, not after. This is the run's one genuinely
        # expensive step and it grows as O(n³) in the label count — 17s at 1,220
        # labels — so a line that only appears on completion means the longest stall
        # in the loop is the one stretch with nothing on screen to explain it.
        logger.info("training this campaign's ranking model on %d judged lead(s)%s "
                    "— the slowest thing a run does, please wait",
                    n, f", {len(self._anchor_X)} of them still synthetic"
                       if self._anchor_X else "")
        started = time.monotonic()
        self._pipeline.fit(X_fit, y_fit)
        lml = self._pipeline.named_steps['gpr'].log_marginal_likelihood_value_

        self._fitted = True
        logger.info("ranking model trained on %d judged lead(s) in %.1fs",
                    n, time.monotonic() - started)
        logger.debug("GPR fitted on %d observations (%d anchors, %d after balancing, "
                      "LML=%.2f)", self.n_obs, len(self._anchor_X), n, lml)
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
        """Persist the fitted pipeline to the Campaign.model_blob DB field."""
        if self._campaign is None or self._pipeline is None:
            return
        import io
        import joblib

        buf = io.BytesIO()
        joblib.dump(self._pipeline, buf, compress=3)
        self._campaign.model_blob = buf.getvalue()
        self._campaign.save(update_fields=["model_blob"])
        logger.debug("Pipeline saved to DB for campaign %s", self._campaign)

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

    def posterior_std(self, embeddings: np.ndarray) -> np.ndarray | None:
        """GP posterior std at each embedding — the uncertainty BALD rewards.

        The explore prefilter ranks candidate queries by summed per-clause variance
        (posterior_std²) as a cheap BALD proxy, so only the top slice is exact-embedded.
        Returns None when the model cannot be fitted yet.
        """
        if not self._fit_if_needed():
            return None
        _, std = _gpr_predict(self._pipeline, embeddings)
        return std

    def acquisition_mode(self, embeddings: np.ndarray | None = None) -> str | None:
        """The live acquisition axis: ``"exploit (p)"``, ``"explore (BALD)"``, or None.

        **Cold phase — always exploit.** While any anchor is still padding the positive
        class, the campaign has exactly one goal: *more real positives*, because each one
        displaces an invented one and the last of them ends the phase, turning every
        downstream ranking into one backed by ground truth. Exploit serves it directly —
        the highest-P lead is the one most like the ideal profile, so it is the likeliest
        genuine fit.

        The class balance cannot decide the axis during that phase, and it is not merely
        that it would decide wrong: the anchors are *held at* the shortfall, so
        ``n_neg > n_pos`` is false by construction and the balance has no information in
        it at all.

        BALD serves the opposite. Information gain is the right objective when both
        classes are real and the question is where the boundary sits; with invented
        positives it spends every LLM call on the lead the model is *most confused*
        about, which is precisely the lead least like the ICP. A live run made that
        concrete — four consecutive picks at P(f>0.5) ≈ 0.25–0.42, and the verdicts were
        veterinary services, cybersecurity education, K-12 tutoring and a metaverse
        product manager, against a health-and-wellness ICP. Every one an accurate
        rejection, and not one of them a step toward the first acceptance.

        Past the cold phase it is balance-driven as before: exploit once real negatives
        outnumber real positives, explore while the classes are still even.

        None on cold start (model not fitted yet). Exposed so callers can pick which
        cheap prefilter to run *before* they exact-embed.
        """
        if not self._fit_if_needed():
            return None
        if self.is_cold:
            return "exploit (p)"
        n_neg, n_pos = self.class_counts
        return "exploit (p)" if n_neg > n_pos else "explore (BALD)"

    def acquisition_scores(self, embeddings: np.ndarray) -> tuple[str, np.ndarray] | None:
        """Score candidates using the balance-driven acquisition strategy.

        - Exploit mode (n_neg > n_pos): returns predicted probabilities P(f > 0.5)
        - Explore mode: returns BALD information gain scores

        Returns ``(strategy_name, scores)`` or ``None`` on cold start.
        """
        strategy = self.acquisition_mode()
        if strategy is None:
            return None
        scores = self.predict_probs(embeddings) if strategy == "exploit (p)" \
            else self.compute_bald(embeddings)
        return strategy, scores

    # ------------------------------------------------------------------
    # Ranking & explain  (raw GP mean — no _prob_above_half)
    # ------------------------------------------------------------------

    def rank_profiles(self, profiles: list) -> list:
        """Rank QUALIFIED profiles by raw GP mean (descending).

        Returns ``[]`` on cold start (model not fitted yet).
        """
        if not profiles:
            return []
        if not self._fit_if_needed():
            logger.debug("rank_profiles: GPR not fitted (%d obs) — returning empty", self.n_obs)
            return []
        return _rank_by_score(profiles, self._pipeline)

    def explain(self, profile: dict) -> str:
        """Human-readable compact scoring explanation."""
        from openoutreach.crm.models import Lead

        lead = Lead.objects.filter(pk=profile.get("lead_id")).first()
        emb = lead.embedding_array if lead else None
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
        """Bulk-load historical labels and fit once.

        Replaces the *real* observations only — anchors are set separately and after,
        so the daemon's boot order (warm_start, then anchor an all-negative campaign)
        holds regardless of which runs first.
        """
        self._X = [X[i].astype(np.float64).ravel() for i in range(len(X))]
        self._y = [int(y[i]) for i in range(len(y))]
        self._fitted = False
        if self.n_obs >= 2:
            self._fit_if_needed()


# ``KitQualifier`` stood here — a pre-trained GPR downloaded from HuggingFace, used
# only by the freemium promo campaign, which had no labels of its own to fit on. It
# went with that campaign; every qualifier is now a ``BayesianQualifier`` fitted on
# the operator's own verdicts.


# ── On-demand construction ────────────────────────────────────────


def qualifier_for(campaign):
    """Build this campaign's qualifier, ready to score.

    Built where it is needed and dropped when the caller is done with it, rather
    than warm-started once at boot and held for the life of the process. A resident
    model is a model that silently goes stale: the daemon used to fit every
    campaign's GP at startup, so a label written an hour later did not move the
    posterior until the next restart. Building here costs one fit over the
    campaign's labels — tens to low hundreds of rows — and is always current.

    It used to be able to return ``None``, for the one case where the freemium
    campaign's downloaded kit was unavailable. With that campaign gone there is no
    such case: every campaign fits on its own labels, and a campaign with none fits
    on its anchors.
    """
    from openoutreach.core.conf import CAMPAIGN_CONFIG
    from openoutreach.core.pipeline.icp import ensure_anchors, stored_anchors
    from openoutreach.crm.models import Lead

    qualifier = BayesianQualifier(
        seed=42,
        n_mc_samples=CAMPAIGN_CONFIG["qualification_n_mc_samples"],
        campaign=campaign,
    )
    X, y = Lead.get_labeled_arrays(campaign)
    if len(X) > 0:
        qualifier.warm_start(X, y)

    # Cold phase — the positive class is partly invented, permanently. With no
    # acceptance at all the labels are one class and the GP cannot fit, so generate
    # the anchors; once real positives have started arriving, restore the same
    # stored set rather than inventing more — it never grows or shrinks again.
    anchors = stored_anchors(campaign) if qualifier.has_real_positive else ensure_anchors(campaign)
    if anchors is not None:
        qualifier.set_anchors(anchors)
    return qualifier
