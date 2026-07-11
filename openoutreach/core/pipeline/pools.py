# openoutreach/core/pipeline/pools.py
"""Pool management via composable generators.

Two generators chain via ``next(upstream, None)``:

    find_candidate() = next(ready_source, None)
                            |
                  ready_source  <- pulls from qualify_source
                            |
                 qualify_source  <- qualifies embedded, unlabelled leads

Each qualify_source iteration produces exactly one label, which shifts the GP
model. Rather than draining the whole discovery page before searching again,
qualify_source interleaves the two: in exploit mode, when the unlabelled pool
holds no candidate the model rates above the adaptive threshold, it pages in
fresh leads via ``discover`` *before* spending an LLM call. A dry page (nothing
new to bring in) ends the generator.
"""
from __future__ import annotations

import logging
from typing import Generator

import numpy as np

from openoutreach.core.conf import CAMPAIGN_CONFIG
from openoutreach.core.ml.qualifier import BayesianQualifier
from openoutreach.core.pipeline.discover import discover
from openoutreach.core.pipeline.qualify import fetch_qualification_candidates, run_qualification
from openoutreach.core.pipeline.ready_pool import find_ready_candidate, promote_to_ready

logger = logging.getLogger(__name__)


def _needs_more_discovery(qualifier: BayesianQualifier, candidates) -> bool:
    """True only in exploit mode when no candidate meets the adaptive threshold.

    Effective threshold = max(0, base - 1/sqrt(n_obs)). Stays at zero until
    ~1/base² observations, then gradually rises toward base — favoring
    qualification over discovery early on, and only paging in fresh leads once
    the model is trained enough to say the current pool holds nothing promising.

    Returns False on cold start, explore mode, a degenerate GP, or empty
    candidates — in all of those, qualifying from the existing pool is the move.
    """
    if not candidates:
        return False

    n_neg, n_pos = qualifier.class_counts
    if n_neg <= n_pos:
        # explore mode — no need to page in more high-P profiles
        return False

    embeddings = np.array([c.embedding_array for c in candidates], dtype=np.float32)
    probs = qualifier.predict_probs(embeddings)
    if probs is None:
        # cold start
        return False

    # If the GP can't differentiate profiles (all predictions identical),
    # discovering won't help — qualify from the existing pool to build labels.
    if len(probs) > 1 and np.ptp(probs) < 1e-6:
        logger.debug(
            "GP predictions degenerate (all ~%.3f) with %d obs — "
            "skipping discovery, qualifying from existing pool",
            float(probs[0]), qualifier.n_obs,
        )
        return False

    base = CAMPAIGN_CONFIG["min_positive_pool_prob"]
    n = qualifier.n_obs
    threshold = max(0.0, base - 1 / np.sqrt(n)) if n > 0 else 0.0
    if bool(np.any(probs >= threshold)):
        return False

    logger.info(
        "Pool (%d unlabelled) has no P >= %.3f in exploit mode "
        "(neg=%d, pos=%d, n_obs=%d, base=%.2f) — paging in fresh leads",
        len(candidates), threshold, n_neg, n_pos, n, base,
    )
    return True


def qualify_source(session, qualifier: BayesianQualifier) -> Generator[str, None, None]:
    """Yield profile_urls from run_qualification(), interleaving discovery.

    Every yield produces a label that shifts the GP model. Before qualifying,
    the source pages in fresh leads whenever the pool is empty or — in exploit
    mode — holds nothing above the adaptive threshold, so discovery and
    qualification stay interleaved instead of qualifying the whole page first.
    A dry discovery page (nothing new) ends the generator.
    """
    while True:
        candidates = fetch_qualification_candidates(session)

        # Empty pool → bring leads in, or stop if discovery is exhausted.
        if not candidates:
            if discover(session) <= 0:
                return
            continue

        # Exploit mode with no promising candidate → page in fresh leads before
        # spending an LLM call, until a promising lead appears or discovery dries up.
        while _needs_more_discovery(qualifier, candidates):
            if discover(session) <= 0:
                break
            candidates = fetch_qualification_candidates(session)

        result = run_qualification(session, qualifier)
        if result is not None:
            yield result
            continue

        # No qualifiable candidate this pass (e.g. all lacked profile text) →
        # one more page before giving up.
        if discover(session) > 0:
            continue
        return


def ready_source(session, qualifier: BayesianQualifier, threshold: float | None = None) -> Generator[dict, None, None]:
    """Yield ready-to-find-email candidates, pulling from qualify when needed."""
    if threshold is None:
        threshold = CAMPAIGN_CONFIG["min_gp_confidence"]
    qualify = qualify_source(session, qualifier)

    while True:
        candidate = find_ready_candidate(session, qualifier)
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


def find_candidate(session, qualifier: BayesianQualifier) -> dict | None:
    """Top profile ready for the paid email lookup, backfilling if needed."""
    return next(ready_source(session, qualifier), None)
