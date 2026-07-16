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
holds no candidate the model rates above the adaptive threshold, it qualifies
one, re-checks with the moved model, and only then walks the frontier once via
``discover``. A dry page (nothing new to bring in) ends the generator.
"""
from __future__ import annotations

import logging
from typing import Generator

import numpy as np
from termcolor import colored

from openoutreach.core.conf import CAMPAIGN_CONFIG
from openoutreach.core.ml.qualifier import BayesianQualifier
from openoutreach.core.pipeline.discover import discover
from openoutreach.core.pipeline.qualify import fetch_qualification_candidates, run_qualification
from openoutreach.core.pipeline.ready_pool import find_ready_candidate, promote_to_ready

logger = logging.getLogger(__name__)


def _needs_more_discovery(qualifier: BayesianQualifier, candidates) -> bool:
    """True in exploit mode when no candidate scores as well as a proven positive.

    The bar is the GP's own score at ``positive_pool_percentile`` of the leads
    that actually qualified — so "promising" means *promising like the ones that
    worked*, in the model's own units, and re-fits itself as it learns. An
    absolute cutoff cannot do this job: ``predict_probs`` is P(latent f > 0.5),
    not a calibrated rate, so the same number means a different thing in every
    campaign and at every training size.

    Returns False on cold start, explore mode, a degenerate GP, empty candidates,
    or before any positive exists — in all of those, qualifying from the existing
    pool is the move (with no proven positive there is no bar to hold anyone to).
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

    pct = CAMPAIGN_CONFIG["positive_pool_percentile"]
    bar = qualifier.positive_score_floor(pct)
    if bar is None:
        # No proven positive yet — nothing to hold the pool to. Qualify to find one.
        return False
    if bool(np.any(probs >= bar)):
        return False

    logger.info(
        "Pool (%d unlabelled) tops out at %s, under the p%g bar of %s set by its "
        "%d proven positive(s) (neg=%d, n_obs=%d) — %s",
        len(candidates), colored(f"{float(probs.max()):.3f}", "yellow", attrs=["bold"]),
        pct, colored(f"{bar:.3f}", "green", attrs=["bold"]),
        n_pos, n_neg, qualifier.n_obs,
        colored("widening the frontier", "yellow", attrs=["bold"]),
    )
    return True


def qualify_source(session, qualifier: BayesianQualifier) -> Generator[str, None, None]:
    """Yield profile_urls from run_qualification(), interleaving discovery.

    Every yield produces a label that shifts the GP model. When the pool holds
    nothing promising, qualify **one** and re-check before widening: that label
    moves the GP, and the re-check asks the moved model whether the pool is still
    barren. Only a pool that stays barren *after* learning something earns a
    frontier move — one per label, never a burst.

    The order matters both ways. Widening without labelling walks blind, since
    the frontier's re-rank scores its nodes with this same GP. Labelling without
    re-checking spends the move on a verdict the new label may have already
    overturned.

    A dry discovery page (nothing new) on an empty pool ends the generator.
    """
    while True:
        candidates = fetch_qualification_candidates(session)

        # Empty pool → bring leads in, or stop if discovery is exhausted.
        if not candidates:
            if discover(session, qualifier) <= 0:
                return
            continue

        unpromising = _needs_more_discovery(qualifier, candidates)
        result = run_qualification(session, qualifier)

        if result is not None:
            # The label just moved the GP. Ask it again before widening: one
            # frontier move per label, and only if the pool is still barren.
            if unpromising and _needs_more_discovery(
                qualifier, fetch_qualification_candidates(session)
            ):
                discover(session, qualifier)
            yield result
            continue

        # No qualifiable candidate this pass (e.g. all lacked profile text) →
        # one more page before giving up.
        if discover(session, qualifier) > 0:
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
