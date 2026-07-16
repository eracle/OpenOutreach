# openoutreach/core/pipeline/pools.py
"""Pool management via composable generators.

Two generators chain via ``next(upstream, None)``:

    find_candidate() = next(ready_source, None)
                            |
                  ready_source  <- pulls from qualify_source
                            |
                 qualify_source  <- qualifies embedded, unlabelled leads

Each qualify_source iteration produces exactly one label, which shifts the GP
model. **Discovery runs only when the pool is dry.** A dry page (nothing new to
bring in) ends the generator.

There is deliberately no "is this pool promising?" gate. Two have been tried and
both were constants in disguise, because every such gate compares a candidate's
score against a bar the GP's scale cannot support:

- ``max(0, 0.20 - 1/sqrt(n_obs))`` — capped at 0.20, while the pool's best lead
  scores 0.327. Never fired: always "pool's fine".
- ``positive_score_floor(25)`` — the p25 of the GP's scores for its own
  positives, i.e. **in-sample** predictions (0.755–0.829) used as a bar for
  **out-of-sample** ones (0.121–0.327). Fired always: "pool is barren", every
  cycle, so discovery ran 17x ahead of qualification.

A fitted GP reproduces its training points and regresses everything unseen toward
the prior, so the two populations never overlap and no bar drawn from one applies
to the other. Discovery steering does not belong here at all — it belongs to the
frontier, on ground-truth per-node counts. See the discovery-query-graph-search
roadmap card.
"""
from __future__ import annotations

import logging
from typing import Generator

from openoutreach.core.conf import CAMPAIGN_CONFIG
from openoutreach.core.ml.qualifier import BayesianQualifier
from openoutreach.core.pipeline.discover import discover
from openoutreach.core.pipeline.qualify import fetch_qualification_candidates, run_qualification
from openoutreach.core.pipeline.ready_pool import find_ready_candidate, promote_to_ready

logger = logging.getLogger(__name__)


def qualify_source(session, qualifier: BayesianQualifier) -> Generator[str, None, None]:
    """Yield profile_urls from run_qualification(), discovering when the pool is dry.

    Every yield produces a label that shifts the GP model. Discovery is pulled in
    only when there is nothing left to qualify — the pool is drained first, which
    is what feeds the frontier's per-node counts (a node nobody examined has no
    measured value; see the module docstring on why no "promising pool" gate
    survives).

    A dry discovery page (nothing new) on an empty pool ends the generator.
    """
    while True:
        candidates = fetch_qualification_candidates(session)

        # Empty pool → bring leads in, or stop if discovery is exhausted.
        if not candidates:
            if discover(session, qualifier) <= 0:
                return
            continue

        result = run_qualification(session, qualifier)
        if result is not None:
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
