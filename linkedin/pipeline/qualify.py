# linkedin/pipeline/qualify.py
"""Qualify orchestration for the lazy chain."""
from __future__ import annotations

import logging

import numpy as np
from django.utils import timezone
from termcolor import colored

from linkedin.conf import CAMPAIGN_CONFIG
from linkedin.ml.qualifier import BayesianQualifier

logger = logging.getLogger(__name__)


def _get_unlabeled_candidates(session):
    """Return unlabeled ProfileEmbedding rows, embedding one new lead if the list is empty."""
    from linkedin.db.crm_profiles import get_leads_for_qualification, ensure_profile_embedded
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


def qualify_one(session, qualifier: BayesianQualifier) -> str | None:
    """Qualify one unlabelled profile via BALD/auto-decision/LLM. Returns public_id or None."""
    from linkedin.ml.qualifier import qualify_with_llm, format_prediction

    candidates = _get_unlabeled_candidates(session)
    if not candidates:
        return None

    logger.info(colored("\u25b6 qualify", "blue", attrs=["bold"]))

    cfg = CAMPAIGN_CONFIG
    entropy_threshold = cfg["qualification_entropy_threshold"]
    max_auto_std = cfg["qualification_max_auto_std"]
    min_accept_prob = cfg["qualification_min_auto_accept_prob"]

    # Balance-driven candidate selection
    selection_score = None
    if len(candidates) == 1:
        candidate = candidates[0]
    else:
        embeddings = np.array([c.embedding_array for c in candidates], dtype=np.float32)
        n_neg, n_pos = qualifier.class_counts

        if n_neg > n_pos:
            scores = qualifier.predict_probs(embeddings)
            strategy = "exploit (p)"
        else:
            scores = qualifier.compute_bald(embeddings)
            strategy = "explore (BALD)"

        if scores is None:
            candidate = candidates[0]
        else:
            best_idx = int(np.argmax(scores))
            candidate = candidates[best_idx]
            selection_score = (strategy, float(scores[best_idx]))
            logger.info("Strategy: %s (neg=%d, pos=%d)",
                        colored(strategy, "cyan", attrs=["bold"]), n_neg, n_pos)

    lead_id = candidate.lead_id
    public_id = candidate.public_identifier
    embedding = candidate.embedding_array

    result = qualifier.predict(embedding)

    if result is not None:
        pred_prob, entropy, std = result
        if entropy < entropy_threshold and std < max_auto_std:
            label = 1 if pred_prob >= min_accept_prob else 0
            decision = "Auto-accepted" if label == 1 else "Auto-rejected"
            reason = (
                f"{decision} by GP model ({qualifier.n_obs} labels). "
                f"prob={pred_prob:.1%}, std={std:.4f}, entropy={entropy:.4f}."
            )
            _record_decision(session, qualifier, lead_id, public_id, embedding, label, reason)
            return public_id

        stats = format_prediction(pred_prob, entropy, std, qualifier.n_obs)
        sel = f", {selection_score[0]}={selection_score[1]:.4f}" if selection_score else ""
        logger.debug("%s uncertain (%s%s) \u2014 querying LLM", public_id, stats, sel)
    else:
        logger.debug("%s GPC not fitted (%d obs) \u2014 querying LLM", public_id, qualifier.n_obs)

    profile_text = _get_profile_text(session, lead_id, public_id)
    if not profile_text:
        logger.warning("No profile text for lead %d \u2014 disqualifying", lead_id)
        _record_decision(session, qualifier, lead_id, public_id, embedding, 0, "no profile text available")
        return public_id

    campaign = session.campaign
    label, reason = qualify_with_llm(
        profile_text,
        product_docs=campaign.product_docs,
        campaign_objective=campaign.campaign_objective,
    )
    _record_decision(session, qualifier, lead_id, public_id, embedding, label, reason)
    return public_id


def _record_decision(session, qualifier: BayesianQualifier, lead_id: int, public_id: str, embedding: np.ndarray, label: int, reason: str):
    from linkedin.db.crm_profiles import disqualify_lead, promote_lead_to_contact
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
            disqualify_lead(session, public_id, reason=str(e))
            return
        logger.info("%s %s: %s", public_id, colored("QUALIFIED", "green", attrs=["bold"]), reason)
    else:
        disqualify_lead(session, public_id, reason=reason)


def _get_profile_text(session, lead_id: int, public_id: str) -> str | None:
    from linkedin.db.crm_profiles import ensure_lead_enriched, lead_profile_by_id
    from linkedin.ml.profile_text import build_profile_text

    ensure_lead_enriched(session, lead_id, public_id)
    profile_data = lead_profile_by_id(lead_id)
    if not profile_data:
        return None
    return build_profile_text({"profile": profile_data})
