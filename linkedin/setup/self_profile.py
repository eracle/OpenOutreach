# linkedin/self_profile.py
"""Discover and persist the logged-in user's own LinkedIn profile."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

ME_URL = "https://www.linkedin.com/in/me/"


def ensure_self_profile(session):
    """Discover the logged-in user's own profile via Voyager API and mark it disqualified.

    Creates two disqualified leads: one for the real profile URL (so auto-discovery
    won't re-enrich it) and a ``/in/me/`` sentinel for subsequent-run detection.
    Neither lead gets an embedding, so the self-profile is never eligible for
    freemium deals.

    Returns the parsed profile dict on first run, or ``None`` on subsequent
    runs (the GDPR check is guarded by its own marker file).
    """
    from crm.models import Lead

    from linkedin.api.client import PlaywrightLinkedinAPI
    from linkedin.db.urls import public_id_to_url

    # Sentinel check — already ran once
    if Lead.objects.filter(website=ME_URL).exists():
        logger.debug("Self-profile already discovered (sentinel exists)")
        return None

    api = PlaywrightLinkedinAPI(session=session)
    profile, data = api.get_profile(public_identifier="me")

    if not profile:
        logger.warning("Could not fetch own profile via Voyager API")
        return None

    real_id = profile["public_identifier"]
    real_url = public_id_to_url(real_id)

    dept = session.campaign.department

    # Disqualified lead for the real profile URL (no embedding).
    # Use update_or_create so auto-discovered leads are also marked disqualified.
    Lead.objects.update_or_create(
        website=real_url,
        defaults={
            "owner": session.django_user,
            "department": dept,
            "first_name": profile.get("first_name", ""),
            "last_name": profile.get("last_name", ""),
            "disqualified": True,
        },
    )
    logger.info("Self-profile discovered: %s", real_url)

    # /in/me/ sentinel — disqualified, used for subsequent-run detection.
    # description stores the real public_identifier as JSON for reverse lookup.
    import json
    Lead.objects.update_or_create(
        website=ME_URL,
        defaults={
            "owner": session.django_user,
            "department": dept,
            "disqualified": True,
            "description": json.dumps({"public_identifier": real_id}),
        },
    )

    return profile
