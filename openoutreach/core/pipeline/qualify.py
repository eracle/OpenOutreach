# openoutreach/linkedin/pipeline/qualify.py
"""Qualify orchestration for the lazy chain."""
from __future__ import annotations

import logging

import numpy as np
from termcolor import colored

from openoutreach.core.ml.qualifier import BayesianQualifier

logger = logging.getLogger(__name__)


def fetch_qualification_candidates(session):
    """Return Lead rows (with embeddings) for leads awaiting qualification."""
    from openoutreach.crm.models import Lead
    from openoutreach.core.db.leads import get_leads_for_qualification

    leads = get_leads_for_qualification(session)
    if not leads:
        return []

    lead_ids = {ld["lead_id"] for ld in leads}

    return list(
        Lead.objects.filter(pk__in=lead_ids, embedding__isnull=False)
        .order_by("creation_date")
    )


def run_qualification(session, qualifier: BayesianQualifier) -> str | None:
    """Qualify one unlabelled profile via BALD/auto-decision/LLM. Returns public_id or None."""
    from openoutreach.core.ml.qualifier import qualify_with_llm, format_prediction

    candidates = fetch_qualification_candidates(session)
    if not candidates:
        return None

    logger.info(colored("▶ qualify", "blue", attrs=["bold"]))

    # Balance-driven candidate selection
    selection_score = None
    if len(candidates) == 1:
        candidate = candidates[0]
    else:
        embeddings = np.array([c.embedding_array for c in candidates], dtype=np.float32)
        result = qualifier.acquisition_scores(embeddings)

        if result is None:
            candidate = candidates[0]
        else:
            strategy, scores = result
            best_idx = int(np.argmax(scores))
            candidate = candidates[best_idx]
            selection_score = (strategy, float(scores[best_idx]))
            n_neg, n_pos = qualifier.class_counts
            logger.info("Strategy: %s (neg=%d, pos=%d)",
                        colored(strategy, "cyan", attrs=["bold"]), n_neg, n_pos)

    lead_id = candidate.pk
    public_id = candidate.public_identifier
    embedding = candidate.embedding_array

    result = qualifier.predict(embedding)

    if result is not None:
        pred_prob, entropy, std = result
        stats = format_prediction(pred_prob, entropy, std, qualifier.n_obs)
        sel = f", {selection_score[0]}={selection_score[1]:.4f}" if selection_score else ""
        logger.debug("%s (%s%s) — querying LLM", public_id, stats, sel)
    else:
        logger.debug("%s GP not fitted (%d obs) — querying LLM", public_id, qualifier.n_obs)

    profile_text = _fetch_profile_text(session, lead_id, public_id)
    if not profile_text:
        # No text source yet — the discovery→qualify wiring (Lead Finder rows)
        # lands in the reshape step. Skip rather than disqualify: a lead we
        # can't read is not a negative fit signal.
        logger.debug("No profile text for lead %d — skipping qualification", lead_id)
        return None

    campaign = session.campaign
    label, reason = qualify_with_llm(
        profile_text,
        product_docs=campaign.product_docs,
        campaign_objective=campaign.campaign_objective,
    )
    _save_qualification_result(session, qualifier, lead_id, public_id, embedding, label, reason)
    return public_id


def _save_qualification_result(session, qualifier: BayesianQualifier, lead_id: int, public_id: str, embedding: np.ndarray, label: int, reason: str):
    # LLM rejections are tracked as FAILED Deals with "Disqualified" closing reason
    # (campaign-scoped), not as Lead.disqualified (permanent account-level exclusion).
    #
    # A hit leaves the Deal QUALIFIED. The GP rank gate (ready_pool) then promotes
    # it to READY_TO_FIND_EMAIL, where the find_email leg spends a BetterContact
    # credit and routes a hit onward to READY_TO_EMAIL. Enrichment is no longer
    # inline at qualification — it sits behind the rank gate, so a credit is only
    # ever spent on a ranked lead.
    from openoutreach.core.db.deals import create_disqualified_deal
    from openoutreach.core.db.leads import promote_lead_to_deal

    qualifier.update(embedding, label)

    if label == 1:
        try:
            promote_lead_to_deal(session, public_id, reason=reason)
        except ValueError as e:
            logger.warning("Cannot promote %s: %s — disqualifying", public_id, e)
            create_disqualified_deal(session, public_id, reason=str(e))
            return
        logger.info("%s %s: %s", public_id, colored("QUALIFIED", "green", attrs=["bold"]), reason)
    else:
        create_disqualified_deal(session, public_id, reason=reason)


def _fetch_profile_text(session, lead_id: int, public_id: str) -> str | None:
    """Text for the LLM qualifier. Sourced from the Lead Finder row once the
    discovery→qualify reshape lands; until then there is no persisted text
    source, so qualification is skipped (see ``run_qualification``)."""
    return None
