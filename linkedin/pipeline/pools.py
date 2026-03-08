# linkedin/pipeline/pools.py
"""Pool management and backfill orchestration.

Stateless functions for querying the qualified-profile pool (with GPR
threshold filtering) and orchestrating the qualify→search backfill
chain when the pool is empty.
"""
from __future__ import annotations

import logging

from linkedin.conf import CAMPAIGN_CONFIG
from linkedin.db.crm_profiles import get_qualified_profiles, load_embedding
from linkedin.pipeline.qualify import qualify_one
from linkedin.pipeline.search import search_one
from linkedin.ml.qualifier import BayesianQualifier

logger = logging.getLogger(__name__)

def top_above_threshold(session, qualifier: BayesianQualifier, min_prob: float, pipeline=None) -> dict | None:
    """Return the top ranked qualified profile above *min_prob*, or None."""
    profiles = get_qualified_profiles(session)
    if not profiles:
        return None

    ranked = qualifier.rank_profiles(profiles, session=session, pipeline=pipeline)
    if not ranked:
        return None

    top = ranked[0]
    emb = load_embedding(top.get("lead_id"), top.get("public_identifier"), session)
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
    while True:
        candidate = top_above_threshold(session, qualifier, min_prob, pipeline=pipeline)
        if candidate is not None:
            return candidate

        if is_partner:
            return None

        if qualify_one(session, qualifier) is not None:
            continue
        if search_one(session) is not None:
            continue
        return None
