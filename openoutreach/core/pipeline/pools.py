# openoutreach/core/pipeline/pools.py
"""The qualify/discover engine that feeds the paid email lookup.

``find_candidate`` is the one entry point: it hands back the top lead ready for a
BetterContact credit, doing whatever qualification and discovery it takes to surface
one. It loops over three moves, cheapest first:

1. a lead already sitting in READY_TO_FIND_EMAIL → hand it off;
2. a QUALIFIED lead clearing the spend gate → promote it (``promote_to_ready``);
3. otherwise ``_advance`` — spend one unit of work labelling or discovering — and loop.

``_advance`` is the whole steering, and it is just the qualifier's own explore/exploit
split (``acquisition_mode``, driven by class balance):

- **explore** (``neg ≤ pos``, or cold start) — label the most *informative* lead in the
  pool (max BALD). No gate: a low-confidence lead is exactly the label that teaches the
  GP the most, so filtering by confidence here would throw away the point of exploring.
  If the pool is empty, discover a page first (there's always a max-BALD lead unless
  there are no leads at all).
- **exploit** (``neg > pos``) — spend the LLM call on a lead that will actually convert:
  the strongest lead clearing ``min_gp_confidence`` (``consumable_candidates``). If none
  clears it, there's nothing worth qualifying — discover more leads instead.

The gate is ``min_gp_confidence`` — the **same constant** ``promote_to_ready`` uses, so a
lead the exploit branch qualifies is one the promote gate will then pass. It is a *spend*
gate: "will this LLM call buy an email, or just park at QUALIFIED?" It belongs to exploit
alone. Explore wants labels, not emails, so it never consults the gate — the earlier
design applied it in both states and so ran BALD over the confidence-filtered set, i.e.
picked the most-uncertain lead from a bucket it had just stripped of uncertain leads.

Discovery is free (Lead Finder bills nothing); the paid BetterContact credit is spent
downstream, in the ``find_email`` task, only on a lead this engine already promoted.
"""
from __future__ import annotations

import logging

import numpy as np

from openoutreach.core.conf import CAMPAIGN_CONFIG
from openoutreach.core.ml.qualifier import BayesianQualifier
from openoutreach.core.pipeline.discover import discover
from openoutreach.core.pipeline.qualify import fetch_qualification_candidates, run_qualification
from openoutreach.core.pipeline.ready_pool import find_ready_candidate, promote_to_ready

logger = logging.getLogger(__name__)


def consumable_candidates(qualifier: BayesianQualifier, candidates: list) -> list:
    """The candidates clearing the spend gate — the ones a qualification can convert.

    Empty means exploit has nothing to convert (so it should widen instead): either
    the GP is unfitted or no lead reaches ``min_gp_confidence``, the same constant the
    promote gate uses.
    """
    if not candidates:
        return []

    X = np.array([c.embedding_array for c in candidates], dtype=np.float64)
    probs = qualifier.predict_probs(X)
    if probs is None:
        return []
    threshold = CAMPAIGN_CONFIG["min_gp_confidence"]
    return [c for c, p in zip(candidates, probs) if p >= threshold]


def _advance(session, qualifier: BayesianQualifier) -> bool:
    """Spend one unit of work — label a lead or discover leads. Returns whether it did.

    Explore vs exploit is the qualifier's balance-driven acquisition mode; see the
    module docstring. Returns False only when the engine has nothing left to do:
    nothing worth labelling and nothing left to discover.
    """
    candidates = fetch_qualification_candidates(session)

    # Exploit — convert a lead that clears the spend gate, else go find more.
    if qualifier.acquisition_mode() == "exploit (p)":
        consumable = consumable_candidates(qualifier, candidates)
        if consumable:
            return run_qualification(session, qualifier, candidates=consumable) is not None
        return discover(session, qualifier) > 0

    # Explore / cold start — label the most informative lead we have (max BALD, no
    # gate). An empty pool is the one case with no lead to label: page one in first.
    if not candidates:
        if discover(session, qualifier) <= 0:
            return False
        candidates = fetch_qualification_candidates(session)
    return run_qualification(session, qualifier, candidates=candidates) is not None


def find_candidate(session, qualifier: BayesianQualifier) -> dict | None:
    """Top lead ready for the paid email lookup, or None when the engine stalls.

    Advances the qualify/discover engine until a lead reaches READY_TO_FIND_EMAIL or
    there is nothing left to label or discover.
    """
    while True:
        candidate = find_ready_candidate(session, qualifier)
        if candidate is not None:
            return candidate

        if promote_to_ready(session, qualifier) > 0:
            continue

        if not _advance(session, qualifier):
            return None
