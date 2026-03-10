# linkedin/pipeline/pools.py
"""Pool management via composable generators.

Three generators chain via next(upstream, None):

    get_candidate() = next(ready_source, None)
                            |
                  ready_source  <- pulls from qualify_source
                            |
                 qualify_source  <- pulls from search_source
                  (positive pool check: search once if exploit mode has no P > 0.5)
                            |
                  search_source  <- yields keywords (never truly exhausts)

Each qualify_source iteration produces exactly one label, which shifts the GP
model — preventing the infinite-search-without-qualifying bug.
"""
from __future__ import annotations

import logging
from typing import Generator

import numpy as np

from linkedin.conf import CAMPAIGN_CONFIG
from linkedin.db.crm_profiles import get_qualified_profiles
from linkedin.ml.qualifier import BayesianQualifier
from linkedin.pipeline.qualify import get_unlabeled_candidates, qualify_one
from linkedin.pipeline.ready_pool import get_ready_candidate, promote_to_ready
from linkedin.pipeline.search import search_one

logger = logging.getLogger(__name__)


def _positive_pool_empty(qualifier: BayesianQualifier, candidates) -> bool:
    """True only in exploit mode when no candidate has P > 0.5.

    Returns False on cold start, explore mode, or empty candidates.
    """
    if not candidates:
        return False

    n_neg, n_pos = qualifier.class_counts
    if n_neg <= n_pos:
        # explore mode — no need to search for high-P profiles
        return False

    embeddings = np.array([c.embedding_array for c in candidates], dtype=np.float32)
    probs = qualifier.predict_probs(embeddings)
    if probs is None:
        # cold start
        return False

    if bool(np.any(probs > 0.5)):
        return False

    logger.info(
        "Pool (%d unlabeled) has no P > 0.5 candidates in exploit mode "
        "(neg=%d, pos=%d, max_P=%.3f) — searching once",
        len(candidates), n_neg, n_pos, float(probs.max()),
    )
    return True


def search_source(session) -> Generator[str, None, None]:
    """Yield keywords from search_one(). Stops when search_one returns None."""
    while True:
        keyword = search_one(session)
        if keyword is None:
            return
        yield keyword


def qualify_source(session, qualifier: BayesianQualifier) -> Generator[str, None, None]:
    """Yield public_ids from qualify_one(), pulling from search when needed.

    Before entering the qualify loop, checks if the pool lacks high-P
    candidates in exploit mode and does ONE search to enrich. Then qualifies
    repeatedly from the pool — every yield produces a label that shifts the
    GP model. Only searches again when the pool is fully empty.
    """
    search = search_source(session)

    # One-time pool quality check: in exploit mode with no P > 0.5
    # candidates, search once to bring in potentially better profiles
    # before starting to qualify.
    candidates = get_unlabeled_candidates(session)
    if _positive_pool_empty(qualifier, candidates):
        next(search, None)

    while True:
        candidates = get_unlabeled_candidates(session)

        # If no candidates at all, search once to bring some in
        if not candidates:
            if next(search, None) is None:
                return
            candidates = get_unlabeled_candidates(session)
            if not candidates:
                return

        result = qualify_one(session, qualifier)
        if result is None:
            return
        yield result


def ready_source(session, qualifier: BayesianQualifier, pipeline=None) -> Generator[dict, None, None]:
    """Yield ready-to-connect candidates, pulling from qualify when needed."""
    threshold = CAMPAIGN_CONFIG["min_ready_to_connect_prob"]
    qualify = qualify_source(session, qualifier)

    while True:
        candidate = get_ready_candidate(session, qualifier, pipeline=pipeline)
        if candidate is not None:
            yield candidate
            continue

        promoted = promote_to_ready(session, qualifier, threshold)
        if promoted > 0:
            continue

        # Pull one qualification from upstream — may shift the GP model
        if next(qualify, None) is not None:
            # Re-check promote after new label
            promote_to_ready(session, qualifier, threshold)
            continue

        # Upstream exhausted
        return


def get_candidate(session, qualifier: BayesianQualifier, pipeline=None, is_partner: bool = False) -> dict | None:
    """Top profile ready for connection, backfilling if needed.

    Partner campaigns bypass READY_TO_CONNECT and pick directly from the QUALIFIED pool.
    Regular campaigns require profiles to pass the GP confidence gate first.
    """
    if is_partner:
        profiles = get_qualified_profiles(session)
        if not profiles:
            return None
        ranked = qualifier.rank_profiles(profiles, session=session, pipeline=pipeline)
        return ranked[0] if ranked else None

    return next(ready_source(session, qualifier, pipeline), None)
