# linkedin/pipeline/ready_pool.py
"""Ready-to-connect pool: GP confidence gate between NEW and READY_TO_CONNECT."""
from __future__ import annotations

import logging

import numpy as np

from linkedin.db.deals import (
    get_qualified_profiles,
    get_ready_to_connect_profiles,
    set_profile_state,
)
from linkedin.ml.qualifier import BayesianQualifier
from linkedin.enums import ProfileState

logger = logging.getLogger(__name__)


def promote_to_ready(session, qualifier: BayesianQualifier, threshold: float) -> int:
    """Promote QUALIFIED profiles above GP confidence threshold to READY_TO_CONNECT.

    Uses an adaptive threshold that starts low and rises toward the configured
    max as more labels accumulate:
        effective = max(0.1, threshold - 2/sqrt(n_obs))

    On cold start (GP not fitted or < 5 observations), all QUALIFIED profiles
    are promoted directly — the LLM already approved them.

    Returns the number of profiles promoted.
    """
    from crm.models import Lead

    profiles = get_qualified_profiles(session)
    if not profiles:
        return 0

    # Cold start: fewer than 5 labels — trust the LLM qualification directly.
    if qualifier.n_obs < 5:
        promoted = 0
        for p in profiles:
            pid = p.get("public_identifier", "?")
            logger.info("%s READY_TO_CONNECT (cold start — LLM-qualified)", pid)
            set_profile_state(session, p["public_identifier"], ProfileState.READY_TO_CONNECT.value)
            promoted += 1
        return promoted

    embeddings = []
    valid = []
    for p in profiles:
        lead = Lead.objects.filter(pk=p.get("lead_id")).first()
        emb = lead.get_embedding(session) if lead else None
        if emb is not None:
            embeddings.append(emb)
            valid.append(p)

    if not valid:
        return 0

    X = np.array(embeddings, dtype=np.float64)
    probs = qualifier.predict_probs(X)
    if probs is None:
        # GP not fitted — promote all qualified
        promoted = 0
        for p in valid:
            pid = p.get("public_identifier", "?")
            logger.info("%s READY_TO_CONNECT (GP not fitted — LLM-qualified)", pid)
            set_profile_state(session, p["public_identifier"], ProfileState.READY_TO_CONNECT.value)
            promoted += 1
        return promoted

    # Adaptive threshold: starts low, rises toward configured max
    n = qualifier.n_obs
    effective_threshold = max(0.1, threshold - 2 / np.sqrt(n)) if n > 0 else 0.1
    if effective_threshold < threshold:
        logger.info(
            "Adaptive threshold: %.3f (target=%.2f, n_obs=%d)",
            effective_threshold, threshold, n,
        )

    promoted = 0
    for prob, p in zip(probs, valid):
        if prob > effective_threshold:
            pid = p.get("public_identifier", "?")
            logger.info("%s READY_TO_CONNECT (P(f>0.5)=%.3f, threshold=%.3f)", pid, prob, effective_threshold)
            set_profile_state(session, p["public_identifier"], ProfileState.READY_TO_CONNECT.value)
            promoted += 1

    return promoted


def find_ready_candidate(session, qualifier: BayesianQualifier) -> dict | None:
    """Return the top-ranked READY_TO_CONNECT profile, or None."""
    profiles = get_ready_to_connect_profiles(session)
    if not profiles:
        return None

    ranked = qualifier.rank_profiles(profiles, session=session)
    return ranked[0] if ranked else None
