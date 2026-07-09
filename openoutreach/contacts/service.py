# openoutreach/contacts/service.py
"""The central contacts store (the hub) — ask the hub before paying BetterContact,
give back what we find.

Two best-effort calls; a missing token or an outage degrades to a no-op and never
breaks outreach. The store caches ``public_identifier -> email`` so the network's
paid + harvested resolutions lower everyone's BetterContact spend as coverage grows.

The geo-gate that keeps EEA/UK/CH out of the store is enforced **server-side** (the
only trusted boundary). The cheap ``is_eea_located`` check here just avoids a
pointless round-trip for a lead we already know is out of scope — it reads the
lead's own ``country_code`` (persisted at discovery), so there is no extra scrape.
"""
from __future__ import annotations

import logging

import requests

from openoutreach.core.models import SiteConfig
from openoutreach.core.geo import is_eea_located

logger = logging.getLogger(__name__)

DEFAULT_API_URL = "https://hub.openoutreach.app"
_TIMEOUT_S = 30

# Where a contributed address came from — the wire values the hub maps to its
# Contribution.Origin (an unrecognized value degrades to "unknown" server-side).
ORIGIN_BETTERCONTACT = "bettercontact"  # paid BetterContact hit
ORIGIN_PROFILE_INFO = "profile_info"  # 1st-degree contact-info overlay


def resolve(lead) -> str | None:
    """A stored email for *lead*, or ``None`` — a miss, no token yet, or an
    outage all return ``None``, so the caller falls back to BetterContact."""
    config = SiteConfig.load()
    if not config.contacts_api_token:
        return None
    try:
        resp = requests.get(
            _endpoint(config, "resolve"),
            params={"id": lead.profile_url},
            headers=_auth(config.contacts_api_token),
            timeout=_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        logger.info("hub: resolve unavailable for %s: %s", lead.profile_url, exc)
        return None
    if resp.status_code not in (200, 404):
        return None  # unexpected → fall back to BetterContact, stay quiet
    # Both hit (200) and miss (404) carry the post-read credit balance; a hit
    # also carries the profile's address(es) as a list (one today, the full
    # dbt-prepared set later), and we send to one, so take the first.
    payload = resp.json()
    credits = payload.get("credits")
    emails = payload.get("emails") or []
    email = emails[0] if emails else None
    if email:
        logger.info("hub: resolved %s for %s (saved a paid lookup) — %s credits available",
                    email, lead.profile_url, credits)
    else:
        logger.info("hub: no stored email for %s — falling back to BetterContact (store balance: %s credits)",
                    lead.profile_url, credits)
    return email


def contribute(session, lead, emails: list[str], origin: str) -> None:
    """Give *lead*'s email(s) to the store — best-effort, non-EU only.

    ``origin`` records where the address came from (``ORIGIN_BETTERCONTACT`` /
    ``ORIGIN_PROFILE_INFO``). The first contribution registers and mints the
    operator's token (kept in the instance's own config, never the repo); later
    ones reuse it.

    Honors the operator's jurisdiction: an EEA/UK/CH operator does not contribute
    (derived from their onboarding country, ``not is_eea_located``), so the whole
    give-back is skipped (no email, no vector — and so no give-to-get credit).
    """
    from openoutreach.core.models import SiteConfig

    if is_eea_located(SiteConfig.load().country_code):
        logger.debug("hub: operator in EEA/UK/CH — skipping give-back for %s", lead.profile_url)
        return
    emails = [e for e in emails if e]
    if not emails:
        logger.debug("hub: nothing to contribute for %s — no email captured", lead.profile_url)
        return
    if is_eea_located(lead.country_code):
        logger.debug("hub: skipping %s (%s) — EEA/UK/CH lead, out of store scope",
                     lead.profile_url, lead.country_code)
        return

    config = SiteConfig.load()
    record = {
        "public_identifier": lead.profile_url,
        "country_code": lead.country_code,
        "emails": emails,
        "origin": origin,
    }
    _attach_embedding(lead, record)
    if config.contacts_api_token:
        _send(config, "contribute", record, lead, headers=_auth(config.contacts_api_token))
    else:
        _register(config, session, record, lead)


def _attach_embedding(lead, record: dict) -> None:
    """Add the cached profile vector to *record*, in place, when it's in hand.

    The operator's opt-in is already checked in ``contribute``, so this only asks
    whether a vector exists. Reads the cached bytes (``lead.embedding``) — never
    ``get_embedding``, which would re-scrape — so a lead that was never embedded
    contributes nothing extra. The 384 floats go on the wire as a JSON list; the
    hub packs them to f16 bytes and validates the length.
    """
    if lead.embedding is None:
        return
    record["embedding"] = lead.embedding_array.tolist()


def _register(config: SiteConfig, session, record: dict, lead) -> None:
    """Mint + persist the operator token via the folded first contribution.

    Keyed to the operator's own email — the single operator identity the hub uses
    for "one token per operator" and as the provenance / revocation handle.
    """
    body = {
        "operator_email": session.django_user.email,
        **record,
    }
    response = _send(config, "register", body, lead)
    token = response.get("token") if response else None
    if not token:
        return
    config.contacts_api_token = token
    config.save(update_fields=["contacts_api_token"])
    logger.info("hub: registered — API token earned and stored")


def _send(config: SiteConfig, path: str, body: dict, lead, headers: dict | None = None) -> dict | None:
    """POST one record; log + swallow any transport failure. Returns the JSON
    body on success, else ``None``."""
    try:
        resp = requests.post(_endpoint(config, path), json=body, headers=headers, timeout=_TIMEOUT_S)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.info("hub: give-back unavailable for %s: %s", lead.profile_url, exc)
        return None
    payload = resp.json()
    logger.info("hub: contributed %s (%s) to the central store — %s credits available",
                lead.profile_url, lead.country_code, payload["credits"])
    return payload


def _endpoint(config: SiteConfig, path: str) -> str:
    base = config.contacts_api_url or DEFAULT_API_URL
    return f"{base.rstrip('/')}/api/v2/{path}/"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
