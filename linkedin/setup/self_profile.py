# linkedin/setup/self_profile.py
"""Discover and persist the logged-in user's own LinkedIn profile."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def discover_self_profile(session) -> dict:
    """Fetch the logged-in user's profile via Voyager API and persist.

    Creates a disqualified Lead for the real profile (so auto-discovery
    won't target it) and links it as ``linkedin_profile.self_lead``.

    Returns the parsed profile dict.
    Raises ``AuthenticationError`` if the API call fails.
    """
    from crm.models import Lead
    from linkedin.api.client import PlaywrightLinkedinAPI
    from linkedin.url_utils import public_id_to_url
    from linkedin.exceptions import AuthenticationError

    api = PlaywrightLinkedinAPI(session=session)
    profile, _raw = api.get_profile(public_identifier="me")

    if not profile:
        raise AuthenticationError("Could not fetch own profile via Voyager API")

    real_id = profile["public_identifier"]
    real_url = public_id_to_url(real_id)

    lead, _ = Lead.objects.update_or_create(
        public_identifier=real_id,
        defaults={
            "linkedin_url": real_url,
            "disqualified": True,
        },
    )
    from linkedin.db.leads import _cache_urn_from_profile
    _cache_urn_from_profile(lead, profile)
    logger.info("Self-profile discovered: %s", real_url)

    session.linkedin_profile.self_lead = lead
    session.linkedin_profile.save(update_fields=["self_lead"])

    return profile
