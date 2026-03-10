# linkedin/pipeline/pools.py
"""Pool management via composable generators.

Three generators chain via next(upstream, None):

    get_candidate() = next(ready_source, None)
                            |
                  ready_source  <- pulls from qualify_source
                            |
                 qualify_source  <- pulls from search_source
                  (keeps searching until P > 0.5 candidates exist in exploit mode)
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
    """True only in exploit mode when no candidate exceeds min_positive_pool_prob.

    Uses P(f > 0.5) > threshold (default 0.25), which expands to
    mean > 0.5 - 0.674 * std — naturally uncertainty-aware via the GP
    posterior.  With few observations std is large so the effective bar
    on the mean is low; as data grows std shrinks and the bar approaches 0.5.

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

    # If the GP can't differentiate profiles (all predictions identical),
    # searching won't help — qualify from existing pool to build up labels.
    if len(probs) > 1 and np.ptp(probs) < 1e-6:
        logger.debug(
            "GP predictions degenerate (all ~%.3f) with %d obs — "
            "skipping search, qualifying from existing pool",
            float(probs[0]), qualifier.n_obs,
        )
        return False

    threshold = CAMPAIGN_CONFIG["min_positive_pool_prob"]
    if bool(np.any(probs > threshold)):
        return False

    logger.info(
        "Pool (%d unlabeled) has no P > %.2f candidates in exploit mode "
        "(neg=%d, pos=%d). "
        "P distribution: min=%.3f, p25=%.3f, median=%.3f, p75=%.3f, max=%.3f",
        len(candidates), threshold, n_neg, n_pos,
        float(np.min(probs)), float(np.percentile(probs, 25)),
        float(np.median(probs)), float(np.percentile(probs, 75)),
        float(np.max(probs)),
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

    In exploit mode, the effective pool is candidates with P > 0.5. When
    this pool is empty, keeps searching until high-P candidates appear or
    search is exhausted. Every yield produces a label that shifts the GP
    model. Only falls through to qualifying low-P candidates when search
    can no longer bring in new profiles.
    """
    search = search_source(session)

    while True:
        candidates = get_unlabeled_candidates(session)

        # If no candidates at all, search to bring some in
        if not candidates:
            if next(search, None) is None:
                return
            candidates = get_unlabeled_candidates(session)
            if not candidates:
                return

        # In exploit mode with no P > 0.5 candidates, keep searching
        # until the positive pool is non-empty or search is exhausted.
        while _positive_pool_empty(qualifier, candidates):
            if next(search, None) is None:
                break
            candidates = get_unlabeled_candidates(session)

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
