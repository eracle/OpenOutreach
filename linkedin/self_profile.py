# linkedin/self_profile.py
"""Discover and persist the logged-in user's own LinkedIn profile."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

ME_URL = "https://www.linkedin.com/in/me/"


def ensure_self_profile(session):
    """Discover the logged-in user's own profile via Voyager API and mark it disqualified.

    Creates a disqualified lead for the real profile URL and a ``/in/me/`` sentinel.
    On subsequent runs the sentinel is detected and the stored profile is
    returned from the CRM.

    Returns the parsed profile dict on first run, or ``None`` on subsequent
    runs (the GDPR check is guarded by its own marker file).
    """
    from crm.models import Lead

    from linkedin.api.client import PlaywrightLinkedinAPI
    from linkedin.db.crm_profiles import (
        create_enriched_lead,
        disqualify_lead,
        public_id_to_url,
    )

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

    # Save and disqualify the real profile
    create_enriched_lead(session, real_url, profile, data)
    disqualify_lead(session, real_id, reason="Own profile")

    # Save the /in/me/ sentinel as disqualified
    dept = session.campaign.department
    Lead.objects.get_or_create(
        website=ME_URL,
        defaults={
            "owner": session.django_user,
            "department": dept,
            "disqualified": True,
        },
    )

    logger.info("Self-profile discovered: %s", real_url)
    return profile
