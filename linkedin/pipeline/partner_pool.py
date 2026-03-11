# linkedin/pipeline/partner_pool.py
"""Partner candidate selection — queries ProfileEmbedding directly, no Deal pre-creation."""
from __future__ import annotations

import json
import logging

from linkedin.db.crm_profiles import url_to_public_id

logger = logging.getLogger(__name__)


def get_partner_candidate(session, qualifier) -> dict | None:
    """Return the top-ranked disqualified+embedded lead not yet dealt in this campaign.

    Bypasses the Deal-based pool system entirely. Queries ProfileEmbedding
    directly, excludes leads that already have a Deal in the partner campaign's
    department, ranks by qualifier, and returns the best one.
    """
    from crm.models import Deal, Lead
    from linkedin.models import ProfileEmbedding

    dept = session.campaign.department

    # All embedded lead IDs
    embedded_pks = set(ProfileEmbedding.objects.values_list("lead_id", flat=True))

    # Exclude leads that already have a Deal in this partner department
    already_dealt = set(
        Deal.objects.filter(department=dept).values_list("lead_id", flat=True)
    )
    eligible_pks = sorted(embedded_pks - already_dealt)

    if not eligible_pks:
        return None

    # Only disqualified leads
    leads = Lead.objects.filter(pk__in=eligible_pks, disqualified=True)
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
