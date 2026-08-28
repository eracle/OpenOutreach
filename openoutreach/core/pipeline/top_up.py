# openoutreach/core/pipeline/top_up.py
"""Filling the pipeline — the one step whose queue is a campaign, not a deal.

Everything else the cycle does is found by querying deals. This cannot be: a lead
nobody has discovered yet has no row to be found. So the bottom of the hierarchy
asks a campaign instead — *do you have enough people waiting?* — and if not, spends
**one** unit of work: label a lead, discover a page, or (in the cold phase) both.

This was the `find_email` handler's hidden second job. Given one queue token it ran
``pools.find_candidate``, a ``while True`` that discovered, LLM-qualified and
promoted until it produced a candidate — anywhere between zero and dozens of LLM
calls behind a single row. Here the loop *is* the cycle: one move per call, and the
caller decides whether to come back.

**The move is the qualifier's own acquisition strategy, unchanged.** This is the
crown jewel and it is ported verbatim:

- **cold phase** (``is_cold`` — invented anchor positives still padding the positive
  class) — do **both** moves every pass: one query in, one label out. Ranking still
  leans on the anchors' *guess* at the ICP, so no observed signal says a label is
  worth more than a page, and a rule that picked one would be a preference dressed
  as a policy. Discovery's return is ignored, so a saturated pool or a provider
  outage still leaves a lead to label; only an empty pool stalls.
- **explore** (``neg ≤ pos``) — label the most *informative* lead (max BALD), with no
  confidence gate: a low-confidence lead is exactly the label that teaches the GP
  most, so filtering by confidence would throw away the point of exploring.
- **exploit** (``neg > pos``) — prefer the strongest lead clearing
  ``min_gp_confidence``, the one whose qualification will buy an email rather than
  park at QUALIFIED. When none clears the gate, label the best lead anyway: the gate
  rations the *paid* credit, not the free LLM call, and a gate on labelling would
  freeze the class balance and deadlock — discovery adds leads but never labels, so
  the GP would never learn what would lift its confidence past the gate.

There used to be a second path here: the freemium promo campaign ran none of the
above, because its leads were already in the account and a downloaded kit model
ranked them. That campaign is gone, so every campaign now takes the one path.
"""
from __future__ import annotations

import logging

import numpy as np
from termcolor import colored

from openoutreach.core.conf import CAMPAIGN_CONFIG
from openoutreach.core.ml.qualifier import BayesianQualifier
from openoutreach.core.pipeline.discover import discover
from openoutreach.core.pipeline.icp import ANCHOR_COUNT
from openoutreach.core.pipeline.qualify import fetch_qualification_candidates, run_qualification

logger = logging.getLogger(__name__)


def top_up(campaign, qualifier: BayesianQualifier) -> bool:
    """Spend one unit of work filling *campaign*'s pipeline. Returns whether it did.

    Label a lead, discover leads, or (cold) both. False means the campaign has nothing
    left to do: nothing worth labelling and nothing left to discover.

    ``qualifier`` is the caller's, not one built here: the cycle's scoring row may
    already have fitted this campaign's model in the same action, and the fit is the
    expensive part. See ``cycle._one_model_per_action``.
    """
    if qualifier.is_cold:
        logger.info("  %s cold phase — %d/%d real positive(s), exploiting the anchors' guess",
                   colored("·", "cyan", attrs=["bold"]),
                   qualifier.n_real_positives, ANCHOR_COUNT)
        discover(campaign, qualifier)
        candidates = fetch_qualification_candidates(campaign)
        if not candidates:
            return False
        return run_qualification(campaign, qualifier, candidates=candidates) is not None

    mode = qualifier.acquisition_mode()
    candidates = fetch_qualification_candidates(campaign)

    if mode == "exploit (p)":
        consumable = _consumable_candidates(qualifier, candidates)
        if consumable:
            return run_qualification(campaign, qualifier, candidates=consumable) is not None
        if candidates:
            return run_qualification(campaign, qualifier, candidates=candidates) is not None
        return discover(campaign, qualifier) > 0

    # Explore — label the most informative lead we have. The GP is fitted here, so it
    # ranks the pool and there is a best lead to pick; an empty pool is the one case
    # with no lead to label, so page one in first.
    if not candidates:
        if discover(campaign, qualifier) <= 0:
            return False
        candidates = fetch_qualification_candidates(campaign)
    return run_qualification(campaign, qualifier, candidates=candidates) is not None


def _consumable_candidates(qualifier: BayesianQualifier, candidates: list) -> list:
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
