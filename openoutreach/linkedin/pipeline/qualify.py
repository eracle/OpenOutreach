# openoutreach/linkedin/pipeline/qualify.py
"""Qualify orchestration for the lazy chain."""
from __future__ import annotations

import logging

import numpy as np
from termcolor import colored

from openoutreach.linkedin.ml.qualifier import BayesianQualifier

logger = logging.getLogger(__name__)


def fetch_qualification_candidates(session):
    """Return Lead rows (with embeddings) for leads awaiting qualification."""
    from openoutreach.crm.models import Lead
    from openoutreach.linkedin.db.leads import get_leads_for_qualification

    leads = get_leads_for_qualification(session)
    if not leads:
        return []

    lead_ids = {ld["lead_id"] for ld in leads}

    candidates = list(
        Lead.objects.filter(pk__in=lead_ids, embedding__isnull=False)
        .order_by("creation_date")
    )
    if candidates:
        return candidates

    # Robustness fallback: embed any lead that was missed at discovery time
    for ld in leads:
        lead = Lead.objects.filter(pk=ld["lead_id"]).first()
        if not lead or lead.embedding is not None:
            continue
        if lead.get_embedding(session) is not None:
            return [lead]

    return []


def run_qualification(session, qualifier: BayesianQualifier) -> str | None:
    """Qualify one unlabelled profile via BALD/auto-decision/LLM. Returns public_id or None."""
    from openoutreach.linkedin.ml.qualifier import qualify_with_llm, format_prediction

    candidates = fetch_qualification_candidates(session)
    if not candidates:
        return None

    logger.info(colored("\u25b6 qualify", "blue", attrs=["bold"]))

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
        logger.warning("No profile text for lead %d \u2014 disqualifying", lead_id)
        _save_qualification_result(session, qualifier, lead_id, public_id, embedding, 0, "no profile text available")
        return public_id

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
    from openoutreach.core.db.deals import create_disqualified_deal, set_profile_state
    from openoutreach.crm.models import DealState
    from openoutreach.linkedin.db.leads import promote_lead_to_deal

    qualifier.update(embedding, label)

    if label == 1:
        try:
            deal = promote_lead_to_deal(session, public_id, reason=reason)
        except ValueError as e:
            logger.warning("Cannot promote %s: %s \u2014 disqualifying", public_id, e)
            create_disqualified_deal(session, public_id, reason=str(e))
            return
        logger.info("%s %s: %s", public_id, colored("QUALIFIED", "green", attrs=["bold"]), reason)
        # Enrich at the QUALIFIED gate (only qualified leads ever reach here).
        # Router model as an explicit FSM fork — the state IS the routing:
        #   hit  → QUALIFIED → READY_TO_EMAIL (the EMAIL send-queue; this is the
        #          one transition the router makes — it must NOT write
        #          READY_TO_CONNECT, which would bypass the GP confidence gate).
        #   miss / finder-off / couldn't-run → stays QUALIFIED (no-op), so the
        #          GP gate promotes it to READY_TO_CONNECT — the connect funnel is
        #          its only door, and the connection harvests contact info on
        #          acceptance. A miss is free to retry (BetterContact bills only
        #          usable hits).
        if _resolve_email(session, deal.lead):
            set_profile_state(session, public_id, DealState.READY_TO_EMAIL)
    else:
        create_disqualified_deal(session, public_id, reason=reason)


def _resolve_email(session, lead) -> bool:
    """Resolution waterfall: free contacts-store read first, paid finder second.

    A finder hit is given back to the central store (moment 1). Returns whether
    an email was resolved — i.e. whether to route the Deal to READY_TO_EMAIL.
    """
    from openoutreach.contacts import service as contacts

    cached_email = contacts.resolve(lead)
    if cached_email:
        lead.api_email = cached_email
        lead.save(update_fields=["api_email"])
        return True
    if lead.resolve_api_email() is True:
        contacts.contribute(session, lead, [lead.api_email])
        return True
    return False


def _fetch_profile_text(session, lead_id: int, public_id: str) -> str | None:
    from openoutreach.crm.models import Lead
    from openoutreach.linkedin.ml.profile_text import build_profile_text

    lead = Lead.objects.filter(pk=lead_id).first()
    if not lead:
        return None
    profile_data = lead.get_profile(session)
    if not profile_data:
        return None
    return build_profile_text({"profile": profile_data})
