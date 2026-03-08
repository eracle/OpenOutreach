# linkedin/pipeline/pools.py
"""Pool management and backfill orchestration.

Stateless functions for querying the qualified-profile pool (with GPR
threshold filtering) and orchestrating the embed→qualify→search backfill
chain when the pool is empty.
"""
from __future__ import annotations

import logging

from linkedin.conf import CAMPAIGN_CONFIG
from linkedin.db.crm_profiles import get_qualified_profiles
from linkedin.pipeline.qualify import embed_one, qualify_one
from linkedin.pipeline.search import search_one
from linkedin.ml.qualifier import BayesianQualifier

logger = logging.getLogger(__name__)


def top_above_threshold(session, qualifier: BayesianQualifier, min_prob: float, pipeline=None) -> dict | None:
    """Return the top ranked qualified profile above *min_prob*, or None."""
    profiles = get_qualified_profiles(session)
    if not profiles:
        return None

    ranked = qualifier.rank_profiles(profiles, pipeline=pipeline)
    if not ranked:
        return None

    top = ranked[0]
    emb = qualifier._load_embedding(top)
    if emb is not None:
        result = qualifier.predict(emb)
        if result is not None:
            prob, _entropy, _std = result
            if prob < min_prob:
                logger.debug(
                    "Top candidate %s prob=%.3f < %.3f",
                    top.get("public_identifier", "?"), prob, min_prob,
                )
                return None

    return top


def get_candidate(session, qualifier: BayesianQualifier, min_prob: float, pipeline=None, is_partner: bool = False) -> dict | None:
    """Top qualified profile above *min_prob*, backfilling if needed."""
    candidate = top_above_threshold(session, qualifier, min_prob, pipeline=pipeline)
    if candidate is not None:
        return candidate

    if is_partner:
        return None

    # Backfill: embed → qualify → search, then re-check
    if (embed_one(session, qualifier)
            or qualify_one(session, qualifier)
            or search_one(session)) is not None:
        return top_above_threshold(session, qualifier, min_prob, pipeline=pipeline)

    return None
