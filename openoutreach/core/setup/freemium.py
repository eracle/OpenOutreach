# openoutreach/core/setup/freemium.py
"""Freemium campaign creation from kit config."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def profile_url_from_slug(slug: str) -> str:
    """Build the provider profile URL for a kit seed's public-id slug."""
    return f"https://www.linkedin.com/in/{slug}"


def import_freemium_campaign(kit_config: dict):
    """Create or update a freemium Campaign from kit config.

    Adds all active users to the campaign.
    Returns the Campaign instance or None.
    """
    from django.contrib.auth.models import User

    from openoutreach.core.models import Campaign

    campaign_name = kit_config.get("campaign_name", "Freemium Outreach")

    campaign, _ = Campaign.objects.update_or_create(
        name=campaign_name,
        defaults={
            "product_docs": kit_config["product_docs"],
            "campaign_objective": kit_config["campaign_objective"],
            "booking_link": kit_config["booking_link"],
            "is_freemium": True,
            "action_fraction": kit_config["action_fraction"],
        },
    )

    # Add every active operator (the Django user running the daemon) to the campaign.
    for user in User.objects.filter(is_active=True, is_staff=True):
        campaign.users.add(user)

    logger.info("[Freemium] Campaign imported: %s (action_fraction=%.2f)",
               campaign_name, kit_config["action_fraction"])
    return campaign


def seed_profiles(session, kit_config: dict):
    """Seed a Lead + QUALIFIED freemium Deal for each profile listed in kit config.

    The seed's ``profile_url`` is an opaque identity key (never fetched).
    Embeddings are *not* built here: they come from Lead-Finder discovery, so an
    unembedded seed is simply skipped by the kit-ranked freemium pool until
    discovery embeds it (dormant-but-wired).
    """
    from openoutreach.core.db.deals import create_freemium_deal
    from openoutreach.crm.models import Lead

    seed_slugs = kit_config.get("seed_profiles", [])
    if not seed_slugs:
        return

    for slug in seed_slugs:
        url = profile_url_from_slug(slug)
        Lead.objects.get_or_create(profile_url=url)
        create_freemium_deal(session, url)
