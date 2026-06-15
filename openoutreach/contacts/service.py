# openoutreach/contacts/service.py
"""The central contacts store — ask before paying the finder, give back what we find.

Two best-effort calls; a missing token or an outage degrades to a no-op and never
breaks outreach. The store caches ``public_identifier -> email`` so the network's
paid + harvested resolutions lower everyone's finder spend as coverage grows.

The geo-gate that keeps EEA/UK/CH out of the store is enforced **server-side** (the
only trusted boundary). The cheap ``is_eea_located`` check here just avoids a
pointless round-trip for a lead we already know is out of scope — it reads the
lead's own ``country_code`` (persisted at discovery), so there is no extra scrape.
"""
from __future__ import annotations

import logging

import requests

from openoutreach.core.models import SiteConfig
from openoutreach.linkedin.setup.geo import is_eea_located

logger = logging.getLogger(__name__)

DEFAULT_API_URL = "https://hub.openoutreach.app"
_TIMEOUT_S = 30


def resolve(lead) -> str | None:
    """A stored email for *lead*, or ``None`` — a miss, no token yet, or an
    outage all return ``None``, so the caller falls back to the paid finder."""
    config = SiteConfig.load()
    if not config.contacts_api_token:
        return None
    try:
        resp = requests.get(
            _endpoint(config, "resolve"),
            params={"id": lead.public_identifier},
            headers=_auth(config.contacts_api_token),
            timeout=_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        logger.info("contacts: resolve unavailable for %s: %s", lead.public_identifier, exc)
        return None
    if resp.status_code != 200:
        return None  # 404 miss (or anything else) → pay the finder
    email = resp.json().get("email")
    if email:
        logger.info("contacts: resolved %s for %s (saved a paid lookup)", email, lead.public_identifier)
    return email


def contribute(session, lead, emails: list[str]) -> None:
    """Give *lead*'s email(s) to the store — best-effort, non-EU only.

    The first contribution registers and mints the operator's token (kept in the
    instance's own config, never the repo); later ones reuse it.
    """
    emails = [e for e in emails if e]
    if not emails or is_eea_located(lead.country_code):
        return

    config = SiteConfig.load()
    record = {
        "public_identifier": lead.public_identifier,
        "country_code": lead.country_code,
        "emails": emails,
    }
    if config.contacts_api_token:
        _send(config, "contribute", record, lead, headers=_auth(config.contacts_api_token))
    else:
        _register(config, session, record, lead)


def _register(config: SiteConfig, session, record: dict, lead) -> None:
    """Mint + persist the operator token via the folded first contribution.

    Keyed to the operator's own LinkedIn account; ``subscriber_email`` is the
    provenance / revocation handle.
    """
    body = {
        "linkedin_public_id": session.self_profile.get("public_identifier"),
        "subscriber_email": session.django_user.email or session.linkedin_profile.linkedin_username,
        **record,
    }
    response = _send(config, "register", body, lead)
    token = response.get("token") if response else None
    if not token:
        return
    config.contacts_api_token = token
    config.save(update_fields=["contacts_api_token"])
    logger.info("contacts: registered — API token earned and stored")


def _send(config: SiteConfig, path: str, body: dict, lead, headers: dict | None = None) -> dict | None:
    """POST one record; log + swallow any transport failure. Returns the JSON
    body on success, else ``None``."""
    try:
        resp = requests.post(_endpoint(config, path), json=body, headers=headers, timeout=_TIMEOUT_S)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.info("contacts: give-back unavailable for %s: %s", lead.public_identifier, exc)
        return None
    logger.info("contacts: contributed %s (%s) to the central store", lead.public_identifier, lead.country_code)
    return resp.json()


def _endpoint(config: SiteConfig, path: str) -> str:
    base = config.contacts_api_url or DEFAULT_API_URL
    return f"{base.rstrip('/')}/api/{path}/"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
