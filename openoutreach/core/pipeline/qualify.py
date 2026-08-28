# openoutreach/core/pipeline/qualify.py
"""Qualify orchestration for the lazy chain."""
from __future__ import annotations

import logging

import numpy as np
from termcolor import colored

from openoutreach.core.ml.qualifier import BayesianQualifier

logger = logging.getLogger(__name__)


def fetch_qualification_candidates(campaign):
    """Embedded, un-dealt Leads awaiting qualification in this campaign, oldest first.

    Invariant (convention, not DB-enforced): a disqualified lead never gets a NEW
    deal, so every deal-creating query filters ``disqualified=False``.
    """
    from openoutreach.crm.models import Lead

    return list(
        Lead.objects.filter(disqualified=False, embedding__isnull=False)
        .exclude(deal__campaign=campaign)
        .order_by("creation_date")
    )


def run_qualification(campaign, qualifier: BayesianQualifier, candidates=None) -> str | None:
    """Qualify one unlabelled profile via the LLM. Returns profile_url or None.

    ``candidates`` restricts the selection to a caller-chosen subset — the consume
    state passes only the leads that can clear the promote gate, so an LLM call is
    never spent on a lead that would park at QUALIFIED. Defaults to the whole
    unlabelled pool, which is what the explore state wants.

    Which candidate gets the call is the qualifier's balance-driven strategy; the
    verdict itself is always the LLM's. On a cold campaign that strategy runs against a
    GP anchored on synthetic ideal profiles (``icp.generate_anchors``) rather than
    against no model at all.
    """
    from openoutreach.core.ml.qualifier import qualify_with_llm, format_prediction

    if candidates is None:
        candidates = fetch_qualification_candidates(campaign)
    if not candidates:
        return None

    logger.info(colored("▶ qualify", "blue", attrs=["bold"]))

    # Balance-driven candidate selection
    selection_score = None
    if len(candidates) == 1:
        logger.info("  %s only one candidate waiting — nothing to rank between",
                    colored("·", "cyan", attrs=["bold"]))
        candidate = candidates[0]
    else:
        embeddings = np.array([c.embedding_array for c in candidates], dtype=np.float32)
        result = qualifier.acquisition_scores(embeddings)

        if result is None:
            # No posterior at all. An anchored campaign always has one, so this is the
            # degraded path: anchoring failed (LLM outage, no ICP text) and the label
            # set is still single-class. Oldest first — nothing here can rank.
            logger.info("  %s no posterior yet — taking the oldest candidate",
                        colored("·", "cyan", attrs=["bold"]))
            candidate = candidates[0]
        else:
            strategy, scores = result
            best_idx = int(np.argmax(scores))
            candidate = candidates[best_idx]
            selection_score = (strategy, float(scores[best_idx]))
            n_neg, n_pos = qualifier.class_counts
            # Which acquisition strategy picked this candidate is a question an
            # operator watching the run genuinely asks — "is it exploring or
            # exploiting right now, and why" — not just the engine reasoning about
            # itself, so it prints at the level a person reading the log sees.
            logger.info("  %s %s — %d rejected vs %d accepted (incl. %d anchor(s))",
                       colored("·", "cyan", attrs=["bold"]),
                       colored(strategy, "cyan", attrs=["bold"]), n_neg, n_pos, qualifier.n_anchors)

    profile_url = candidate.profile_url
    embedding = candidate.embedding_array

    result = qualifier.predict(embedding)

    if result is not None:
        pred_prob, entropy, std = result
        stats = format_prediction(pred_prob, entropy, std, qualifier.n_obs)
        sel = f", {selection_score[0]}={selection_score[1]:.4f}" if selection_score else ""
        logger.debug("%s (%s%s) — querying LLM", profile_url, stats, sel)
    else:
        logger.debug("%s GP not fitted (%d obs) — querying LLM", profile_url, qualifier.n_obs)

    if not candidate.profile_text:
        # A lead we can't read is not a negative fit signal — skip rather than
        # disqualify (e.g. a pre-pivot lead with no persisted firmographic text).
        logger.debug("No profile text for %s — skipping qualification", profile_url)
        return None

    label, reason = qualify_with_llm(
        candidate.profile_text,
        product_docs=campaign.product_docs,
        campaign_target=campaign.campaign_target,
    )
    _save_qualification_result(campaign, qualifier, candidate, embedding, label, reason)
    return profile_url


def _who(lead) -> str:
    """The lead as the operator would name them, falling back to the URL.

    Every part is nullable — ``NULL`` means the provider never told us — so this
    assembles whatever is there rather than assuming a shape.
    """
    from openoutreach.core.logging import hyperlink

    who = " · ".join(part for part in (
        lead.full_name,
        ", ".join(part for part in (lead.job_title, getattr(lead.company, "name", None)) if part),
    ) if part)
    return who or hyperlink(lead.profile_url)


def _verdict_line(glyph: str, color: str, who: str, reason: str) -> str:
    """One judgement, indented under the ``▶ qualify`` header.

    ``logblock.step_line`` is not the right primitive here: its fixed-width label column
    exists to align short plumbing labels (``bettercontact``, ``hub cache``), and a
    person's name overflows it on every row. The glyph and the name carry the colour; the
    reason stays default-weight, because the eye scans the column of ✓ and ✗ first.
    """
    tint = lambda text: colored(text, color, attrs=["bold"])  # noqa: E731
    return f"  {tint(glyph)}  {tint(who)} — {reason}"


def _save_qualification_result(campaign, qualifier: BayesianQualifier, lead, embedding: np.ndarray, label: int, reason: str):
    # LLM rejections are tracked as FAILED Deals with "Disqualified" closing reason
    # (campaign-scoped), not as Lead.disqualified (permanent account-level exclusion).
    #
    # A hit leaves the Deal QUALIFIED, and QUALIFIED is already exportable — the
    # reason written here is the product. The GP rank gate (ready_pool) then
    # promotes it to READY_TO_FIND_EMAIL, where the enrichment leg spends a
    # BetterContact credit and routes a hit onward to RESOLVED. Enrichment sits
    # behind the rank gate, so a credit is only ever spent on a ranked lead.
    from openoutreach.core.db.deals import create_disqualified_deal
    from openoutreach.core.db.leads import promote_lead_to_deal

    profile_url = lead.profile_url
    qualifier.update(embedding, label)

    # **Both verdicts are printed, and they differ at a glance.** Watching it turn people
    # down is what makes the acceptances credible; a log of nothing but hits reads like a
    # row dump, and the reason it writes is the product either way.
    if label == 1:
        try:
            promote_lead_to_deal(campaign, profile_url, reason=reason)
        except ValueError as e:
            logger.warning("Cannot promote %s: %s — disqualifying", profile_url, e)
            create_disqualified_deal(campaign, profile_url, reason=str(e))
            return
        logger.info("%s", _verdict_line("✓", "green", _who(lead), reason))
    else:
        create_disqualified_deal(campaign, profile_url, reason=reason)
        logger.info("%s", _verdict_line("✗", "yellow", _who(lead), reason))
    logger.debug("%s labelled %d: %s", profile_url, label, reason)
