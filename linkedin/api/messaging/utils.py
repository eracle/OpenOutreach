# linkedin/api/messaging/utils.py
"""Shared helpers for messaging API modules."""
import logging
from urllib.parse import quote

from linkedin.api.client import PlaywrightLinkedinAPI
from linkedin.exceptions import AuthenticationError

logger = logging.getLogger(__name__)


def get_self_urn(api: PlaywrightLinkedinAPI) -> str:
    """Return the authenticated user's fsd_profile URN via the /in/me/ sentinel lead."""
    from crm.models import Lead
    from linkedin.setup.self_profile import ME_URL

    sentinel = Lead.objects.filter(linkedin_url=ME_URL).only("description", "public_identifier").first()
    if sentinel:
        urn = sentinel.get_urn(api.session)
        if urn:
            return urn

    # Fallback: fetch directly
    profile, _ = api.get_profile(public_identifier="me")
    if not profile:
        raise AuthenticationError("Cannot fetch own profile via Voyager API")
    return profile["urn"]


def encode_urn(urn: str) -> str:
    """Percent-encode a URN for use inside Voyager GraphQL variables."""
    return quote(urn, safe="")


def check_response(res, context: str) -> None:
    """Check a Voyager messaging API response, raising on errors."""
    match res.status:
        case 401:
            raise AuthenticationError(f"Messaging API 401 ({context})")
        case 403 | 404:
            raise IOError(f"Messaging API {res.status} ({context})")
    if not res.ok:
        raise IOError(f"Messaging API {res.status} ({context}): {res.text()[:500]}")
