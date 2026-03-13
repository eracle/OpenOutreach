# linkedin/pipeline/qualify.py
"""Qualify orchestration for the lazy chain."""
from __future__ import annotations

import logging

import numpy as np
from django.utils import timezone
from termcolor import colored

from linkedin.ml.qualifier import BayesianQualifier

logger = logging.getLogger(__name__)


def fetch_unlabeled_candidates(session):
    """Return unlabeled ProfileEmbedding rows, embedding one new lead if the list is empty."""
    from linkedin.db.leads import get_leads_for_qualification
    from linkedin.db.enrichment import ensure_profile_embedded
    from linkedin.models import ProfileEmbedding

    leads = get_leads_for_qualification(session)
    if not leads:
        return []

    lead_ids = {ld["lead_id"] for ld in leads}

    candidates = list(
        ProfileEmbedding.objects.filter(lead_id__in=lead_ids, label__isnull=True)
        .order_by("created_at")
    )
    if candidates:
        return candidates

    # Robustness fallback: embed any lead that was missed at discovery time
    embedded_ids = set(
        ProfileEmbedding.objects.filter(lead_id__in=lead_ids)
        .values_list("lead_id", flat=True)
    )
    for ld in leads:
        lid = ld["lead_id"]
        if lid in embedded_ids:
            continue
        if ensure_profile_embedded(lid, ld["public_identifier"], session):
            row = ProfileEmbedding.objects.filter(lead_id=lid, label__isnull=True).first()
            if row:
                return [row]

    return []


def run_qualification(session, qualifier: BayesianQualifier) -> str | None:
    """Qualify one unlabelled profile via BALD/auto-decision/LLM. Returns public_id or None."""
    from linkedin.ml.qualifier import qualify_with_llm, format_prediction

    candidates = fetch_unlabeled_candidates(session)
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

    lead_id = candidate.lead_id
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
    # (campaign-scoped), not as Lead.disqualified (reserved for self-profile exclusion).
    from linkedin.db.deals import create_disqualified_deal
    from linkedin.db.leads import promote_lead_to_contact
    from linkedin.models import ProfileEmbedding

    ProfileEmbedding.objects.filter(lead_id=lead_id).update(
        label=label, llm_reason=reason, labeled_at=timezone.now(),
    )
    qualifier.update(embedding, label)

    if label == 1:
        try:
            promote_lead_to_contact(session, public_id)
        except ValueError as e:
            logger.warning("Cannot promote %s: %s \u2014 disqualifying", public_id, e)
            create_disqualified_deal(session, public_id, reason=str(e))
            return
        logger.info("%s %s: %s", public_id, colored("QUALIFIED", "green", attrs=["bold"]), reason)
    else:
        create_disqualified_deal(session, public_id, reason=reason)


def _fetch_profile_text(session, lead_id: int, public_id: str) -> str | None:
    from linkedin.db.enrichment import ensure_lead_enriched
    from linkedin.db.leads import lead_profile_by_id
    from linkedin.ml.profile_text import build_profile_text

    ensure_lead_enriched(session, lead_id, public_id)
    profile_data = lead_profile_by_id(lead_id)
    if not profile_data:
        return None
    return build_profile_text({"profile": profile_data})
