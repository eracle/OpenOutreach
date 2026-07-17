# openoutreach/core/pipeline/pools.py
"""Pool management via composable generators.

Two generators chain via ``next(upstream, None)``:

    find_candidate() = next(ready_source, None)
                            |
                  ready_source  <- pulls from qualify_source
                            |
                 qualify_source  <- qualifies embedded, unlabelled leads

Each qualify_source iteration produces exactly one label, which shifts the GP
model. ``qualify_source`` runs in one of **two states**, decided per iteration by
whether any unlabelled lead reaches ``min_gp_confidence``:

- **consume** — such leads exist, so qualify them. Having any is what it *means*
  to be out of the cold start: only a lead at or above that score can clear
  ``ready_pool.promote_to_ready`` and reach the paid email step.
- **cold start** — none do. Discover a page and spend exactly one label on the
  pool, which is the only thing that moves the GP. That one lead is chosen
  without the threshold — requiring it would be circular, since nothing meets it.

Both states pick their lead with the qualifier's balance-driven strategy.

The threshold is ``min_gp_confidence`` — deliberately the *same constant* the
promote gate uses, and that is the whole argument for it. This is not a "is this
pool promising?" judgment; it is the fact that a lead below it **parks**. It would
be qualified and then blocked by ``promote_to_ready``, so the LLM call buys a
label and nothing else. In cold start the label is exactly what we want; in
consume we want the email. Two bars that were judgments both failed, and are not
to be reintroduced:

- ``max(0, 0.20 - 1/sqrt(n_obs))`` — capped at 0.20, while the pool's best lead
  scores 0.327. Never fired: always "pool's fine".
- ``positive_score_floor(25)`` — the p25 of the GP's scores for its own
  positives, i.e. **in-sample** predictions (0.755–0.829) used as a bar for
  **out-of-sample** ones (0.121–0.327). Fired always: "pool is barren", every
  cycle, so discovery ran 17x ahead of qualification.

A fitted GP reproduces its training points and regresses everything unseen toward
the prior, so no bar drawn from one population applies to the other. That is why
this gate compares against the *next gate's* constant rather than inventing one.
Discovery steering does not belong here at all — it belongs to the frontier, on
ground-truth per-node counts. See the discovery-query-graph-search roadmap card.

**Measured 2026-07-17: the pool tops out at 0.327, so this runs in cold start and
will keep doing so until many more labels exist.** That is expected — the GP has
4 positives. A lead the LLM accepts during cold start parks at QUALIFIED and is
not emailed; it did its job by contributing a label.
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


def consumable_candidates(qualifier: BayesianQualifier, candidates, threshold: float) -> list:
    """The candidates scoring at or above ``threshold`` — the ones that can reach email.

    Empty means cold start: either the GP is unfitted (``predict_probs`` → None) or
    nothing clears the promote gate, and in both cases there is nothing worth an LLM
    call for its own sake.
    """
    if not candidates:
        return []

    X = np.array([c.embedding_array for c in candidates], dtype=np.float64)
    probs = qualifier.predict_probs(X)
    if probs is None:
        return []
    return [c for c, p in zip(candidates, probs) if p >= threshold]


def qualify_source(session, qualifier: BayesianQualifier,
                   threshold: float | None = None) -> Generator[str, None, None]:
    """Yield profile_urls, one label per iteration, in consume or cold-start state.

    See the module docstring for the two states. The generator ends only when it
    can neither qualify nor discover.
    """
    if threshold is None:
        threshold = CAMPAIGN_CONFIG["min_gp_confidence"]

    while True:
        candidates = fetch_qualification_candidates(session)
        consumable = consumable_candidates(qualifier, candidates, threshold)

        # Consume — these leads can clear the promote gate, so the LLM call buys
        # an email and not just a label.
        if consumable:
            result = run_qualification(session, qualifier, candidates=consumable)
            if result is not None:
                yield result
                continue

        # Cold start — nothing in the pool can reach email. Widen, then spend one
        # label on the whole pool (no threshold: nothing meets it, by definition).
        discovered = discover(session)
        result = run_qualification(session, qualifier)
        if result is not None:
            yield result
            continue
        if discovered <= 0:
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
