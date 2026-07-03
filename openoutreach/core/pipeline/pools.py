# openoutreach/linkedin/pipeline/pools.py
"""Pool management via composable generators.

Two generators chain via ``next(upstream, None)``:

    find_candidate() = next(ready_source, None)
                            |
                  ready_source  <- pulls from qualify_source
                            |
                 qualify_source  <- qualifies embedded, unlabelled leads

Each qualify_source iteration produces exactly one label, which shifts the GP
model. When the unlabelled pool runs dry, qualify_source pulls a fresh page from
Lead Finder discovery (``discover``) and retries, so the chain both brings new
leads in and qualifies them.
"""
from __future__ import annotations

import logging
from typing import Generator

from openoutreach.core.conf import CAMPAIGN_CONFIG
from openoutreach.core.ml.qualifier import BayesianQualifier
from openoutreach.core.pipeline.discover import discover
from openoutreach.core.pipeline.qualify import run_qualification
from openoutreach.core.pipeline.ready_pool import find_ready_candidate, promote_to_ready

logger = logging.getLogger(__name__)


def qualify_source(session, qualifier: BayesianQualifier) -> Generator[str, None, None]:
    """Yield profile_urls from run_qualification() until discovery is exhausted.

    Every yield produces a label that shifts the GP model. When the unlabelled
    pool empties, pull a fresh discovery page and retry; a dry page (nothing new)
    ends the generator.
    """
    while True:
        result = run_qualification(session, qualifier)
        if result is not None:
            yield result
            continue
        if discover(session) > 0:
            continue
        return


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
