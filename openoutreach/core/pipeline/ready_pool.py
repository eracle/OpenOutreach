# openoutreach/core/pipeline/ready_pool.py
"""Find-email pool: GP confidence gate between QUALIFIED and READY_TO_FIND_EMAIL.

The rank gate for the paid action — the browserless replacement for the old
connect gate. It promotes only QUALIFIED leads the GP model is confident about
into READY_TO_FIND_EMAIL, so a BetterContact credit is only ever spent on a
ranked lead.
"""
from __future__ import annotations

import logging

import numpy as np

from openoutreach.core.conf import CAMPAIGN_CONFIG
from openoutreach.core.db.deals import (
    get_qualified_profiles,
    get_ready_to_find_email_profiles,
    set_profile_state,
)
from openoutreach.core.ml.qualifier import BayesianQualifier
from openoutreach.crm.models import DealState

logger = logging.getLogger(__name__)


def acceptance_threshold() -> float:
    """The GP confidence a lead's P(f>0.5) must clear to be worth a paid lookup.

    One definition, shared by the promotion gate (QUALIFIED → READY_TO_FIND_EMAIL)
    and the discovery frontier (which scores a query node by how many of its leads
    would clear this same gate). Tunable via ``CAMPAIGN_CONFIG["min_gp_confidence"]``.
    """
    return CAMPAIGN_CONFIG["min_gp_confidence"]


def count_accepted(qualifier: BayesianQualifier, embeddings: np.ndarray,
                   threshold: float | None = None) -> int | None:
    """How many of ``embeddings`` clear the GP acceptance gate (P(f>0.5) > threshold).

    The frontier's per-node score: the number of a query's leads the GP would
    accept for the paid pipeline. Returns None on cold start (GP not fitted).
    """
    if threshold is None:
        threshold = acceptance_threshold()
    X = np.asarray(embeddings, dtype=np.float64)
    if X.size == 0:
        return 0
    probs = qualifier.predict_probs(X)
    if probs is None:
        return None
    return int(np.count_nonzero(probs > threshold))


def promote_to_ready(session, qualifier: BayesianQualifier, threshold: float) -> int:
    """Promote QUALIFIED profiles above the GP confidence threshold to
    READY_TO_FIND_EMAIL.

    Returns the number of profiles promoted. Returns 0 when the GP model
    is not fitted (cold start) or when no QUALIFIED profiles exist.
    """
    from openoutreach.crm.models import Lead

    profiles = get_qualified_profiles(session)
    if not profiles:
        return 0

    embeddings = []
    valid = []
    for p in profiles:
        lead = Lead.objects.filter(pk=p.get("lead_id")).first()
        emb = lead.embedding_array if lead else None
        if emb is not None:
            embeddings.append(emb)
            valid.append(p)

    if not valid:
        return 0

    X = np.array(embeddings, dtype=np.float64)
    probs = qualifier.predict_probs(X)
    if probs is None:
        return 0

    promoted = 0
    for prob, p in zip(probs, valid):
        if prob > threshold:
            pid = p.get("profile_url", "?")
            logger.info("%s READY_TO_FIND_EMAIL (P(f>0.5)=%.3f)", pid, prob)
            set_profile_state(session, p["profile_url"], DealState.READY_TO_FIND_EMAIL.value)
            promoted += 1

    return promoted


def find_ready_candidate(session, qualifier: BayesianQualifier) -> dict | None:
    """Return the top-ranked READY_TO_FIND_EMAIL profile, or None."""
    profiles = get_ready_to_find_email_profiles(session)
    if not profiles:
        return None

    ranked = qualifier.rank_profiles(profiles)
    return ranked[0] if ranked else None
