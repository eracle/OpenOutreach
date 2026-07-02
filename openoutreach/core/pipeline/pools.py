# openoutreach/linkedin/pipeline/pools.py
"""Pool management via composable generators.

Two generators chain via ``next(upstream, None)``:

    find_candidate() = next(ready_source, None)
                            |
                  ready_source  <- pulls from qualify_source
                            |
                 qualify_source  <- qualifies embedded, unlabelled leads

Each qualify_source iteration produces exactly one label, which shifts the GP
model. Discovery (bringing new leads in) is a separate concern — the Lead Finder
discovery→qualify wiring lands in the reshape step; until then this chain only
qualifies + ranks leads already embedded in the DB.
"""
from __future__ import annotations

import logging
from typing import Generator

from openoutreach.core.conf import CAMPAIGN_CONFIG
from openoutreach.core.ml.qualifier import BayesianQualifier
from openoutreach.core.pipeline.qualify import run_qualification
from openoutreach.core.pipeline.ready_pool import find_ready_candidate, promote_to_ready

logger = logging.getLogger(__name__)


def qualify_source(session, qualifier: BayesianQualifier) -> Generator[str, None, None]:
    """Yield public_ids from run_qualification() until the unlabelled pool is dry.

    Every yield produces a label that shifts the GP model.
    """
    while True:
        result = run_qualification(session, qualifier)
        if result is None:
            return
        yield result


def ready_source(session, qualifier: BayesianQualifier, threshold: float | None = None) -> Generator[dict, None, None]:
    """Yield ready-to-find-email candidates, pulling from qualify when needed."""
    if threshold is None:
        threshold = CAMPAIGN_CONFIG["min_ready_to_connect_prob"]
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
