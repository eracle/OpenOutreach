# linkedin/pipeline/freemium_pool.py
"""Freemium candidate selection — seed profiles (QUALIFIED Deals) first, then undiscovered."""
from __future__ import annotations

import logging

from linkedin.enums import ProfileState

logger = logging.getLogger(__name__)


def find_freemium_candidate(session, qualifier) -> dict | None:
    """Return the top-ranked embedded lead eligible for connection.

    Priority: seed profiles with QUALIFIED Deals are returned first (ranked by
    the kit model).  Once all seeds are exhausted (connected / failed), falls
    back to embedded leads without any Deal in this campaign.
    """
    from crm.models import Deal, Lead

    campaign = session.campaign

    # All embedded lead IDs
    embedded_pks = set(Lead.objects.filter(embedding__isnull=False).values_list("pk", flat=True))

    # Seed profiles: QUALIFIED Deals in this campaign (ready to connect)
    seed_pks = set(
        Deal.objects.filter(campaign=campaign, state=ProfileState.QUALIFIED)
        .values_list("lead_id", flat=True)
    )
    seed_pks &= embedded_pks  # must have embeddings

    # Leads with any Deal in this campaign (all states)
    all_dealt_pks = set(
        Deal.objects.filter(campaign=campaign).values_list("lead_id", flat=True)
    )

    # Undiscovered: embedded leads with no Deal at all in this campaign
    undiscovered_pks = embedded_pks - all_dealt_pks

    # Try seeds first, then undiscovered
    for candidate_pks in (seed_pks, undiscovered_pks):
        if not candidate_pks:
            continue
        result = _pick_best(sorted(candidate_pks), qualifier, session)
        if result:
            return result

    return None


def _pick_best(lead_pks: list[int], qualifier, session) -> dict | None:
    """Rank leads by qualifier and return the top-1 profile dict."""
    from crm.models import Lead

    leads = Lead.objects.filter(pk__in=lead_pks, disqualified=False)
    profiles = [d for lead in leads if (d := lead.to_profile_dict())]

    if not profiles:
        return None

    ranked = qualifier.rank_profiles(profiles, session=session)
    return ranked[0] if ranked else None
