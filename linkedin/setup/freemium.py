# linkedin/setup/freemium.py
"""Freemium campaign creation from kit config."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def import_freemium_campaign(kit_config: dict):
    """Create or update a freemium Campaign from kit config.

    Creates the department, pipeline, and adds all active users to the group.
    Returns the Campaign instance or None.
    """
    from common.models import Department
    from linkedin.management.setup_crm import ensure_campaign_pipeline
    from linkedin.models import Campaign, LinkedInProfile

    dept_name = kit_config.get("campaign_name", "Freemium Outreach")
    dept, _ = Department.objects.get_or_create(name=dept_name)

    ensure_campaign_pipeline(dept)

    campaign, _ = Campaign.objects.update_or_create(
        department=dept,
        defaults={
            "product_docs": kit_config["product_docs"],
            "campaign_objective": kit_config["campaign_objective"],
            "booking_link": kit_config["booking_link"],
            "is_freemium": True,
            "action_fraction": kit_config["action_fraction"],
        },
    )

    # Add all active LinkedIn users to this department group
    for lp in LinkedInProfile.objects.filter(active=True).select_related("user"):
        if dept not in lp.user.groups.all():
            lp.user.groups.add(dept)

    logger.info("[Freemium] Campaign imported: %s (action_fraction=%.2f)",
               dept_name, kit_config["action_fraction"])
    return campaign


def seed_profiles(session, kit_config: dict):
    """Seed Lead + ProfileEmbedding for profiles listed in kit config.

    Reads ``seed_profiles`` (list of public IDs) from the kit config.
    Leads are get-or-created; embeddings are lazily computed via Voyager API.
    """
    from crm.models import Lead

    from linkedin.db._helpers import _get_lead_source
    from linkedin.db.enrichment import ensure_profile_embedded
    from linkedin.db.urls import public_id_to_url

    public_ids = kit_config.get("seed_profiles", [])
    if not public_ids:
        return

    for public_id in public_ids:
        url = public_id_to_url(public_id)

        lead, _ = Lead.objects.get_or_create(
            website=url,
            defaults={
                "owner": session.django_user,
                "department": session.campaign.department,
                "lead_source": _get_lead_source(session),
            },
        )

        ensure_profile_embedded(lead.pk, public_id, session)
