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

    Returns the number of profiles promoted. Returns 0 when the GP model
    is not fitted (cold start) or when no QUALIFIED profiles exist.
    """
    from crm.models import Lead

    profiles = get_qualified_profiles(session)
    if not profiles:
        return 0

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
        return 0

    promoted = 0
    for prob, p in zip(probs, valid):
        if prob > threshold:
            pid = p.get("public_identifier", "?")
            logger.info("%s READY_TO_CONNECT (P(f>0.5)=%.3f)", pid, prob)
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
