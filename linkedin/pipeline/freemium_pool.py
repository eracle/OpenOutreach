# linkedin/pipeline/freemium_pool.py
"""Freemium candidate selection — queries ProfileEmbedding directly, no Deal pre-creation."""
from __future__ import annotations

import json
import logging

from linkedin.db.urls import url_to_public_id

logger = logging.getLogger(__name__)


def find_freemium_candidate(session, qualifier) -> dict | None:
    """Return the top-ranked embedded lead not yet dealt in this campaign.

    Candidate pool: any embedded lead without a Deal in this department,
    excluding self-profile (disqualified=True). LLM rejections in other
    campaigns don't exclude leads from this campaign — each campaign
    (department) maintains independent Deal state.
    """
    from crm.models import Deal, Lead
    from linkedin.models import ProfileEmbedding

    dept = session.campaign.department

    # All embedded lead IDs
    embedded_pks = set(ProfileEmbedding.objects.values_list("lead_id", flat=True))

    # Exclude leads that already have a Deal in this freemium department
    already_dealt = set(
        Deal.objects.filter(department=dept).values_list("lead_id", flat=True)
    )
    eligible_pks = sorted(embedded_pks - already_dealt)

    if not eligible_pks:
        return None

    # disqualified=False excludes self-profile only (account-level exclusion);
    # campaign-scoped rejections are tracked as FAILED Deals, not lead flags.
    leads = Lead.objects.filter(pk__in=eligible_pks, disqualified=False)
    if not leads.exists():
        return None

    # Build profile dicts matching the shape expected by rank_profiles
    profiles = []
    for lead in leads:
        profile = {}
        if lead.description:
            try:
                profile = json.loads(lead.description)
            except (json.JSONDecodeError, TypeError):
                pass
        public_id = url_to_public_id(lead.website) if lead.website else ""
        if not public_id:
            continue
        profiles.append({
            "lead_id": lead.pk,
            "public_identifier": public_id,
            "url": lead.website or "",
            "profile": profile,
            "meta": {},
        })

    if not profiles:
        return None

    ranked = qualifier.rank_profiles(profiles, session=session)
    return ranked[0] if ranked else None
