# openoutreach/emails/bettercontact.py
"""BetterContact email lookup — resolve a work email for a qualified lead.

`resolve_email` is the public entry point: it submits the lookup and waits for
the result over an async submit→poll HTTP contract. `is_configured()` reports
whether an API key is set. A missing key or a miss yields None / raises
`BetterContactUnavailable`, never a bare error, so enrichment can't take down
the daemon. This is the *paid* finder — distinct from the free hub lookup
(`contacts.resolve`), which is tried first.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

_ENRICH_URL = "https://app.bettercontact.rocks/api/v2/async"
_POLL_INTERVAL_S = 5
_POLL_TIMEOUT_S = 300
_HTTP_TIMEOUT_S = 30
_USABLE_STATUSES = frozenset({"valid", "deliverable", "catch_all_safe"})

# Cloudflare 403s a non-browser User-Agent (error 1010), so spoof a browser.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class BetterContactUnavailable(Exception):
    """BetterContact could not run — no API key configured, or the service was
    unreachable. Distinct from a genuine miss (it ran, found no email)."""


@dataclass(frozen=True)
class BetterContactQuery:
    """A lead to resolve. linkedin_url alone works; name/company lift the hit rate."""

    linkedin_url: str
    first_name: str = ""
    last_name: str = ""
    company: str = ""
    company_domain: str = ""


@dataclass(frozen=True)
class BetterContactResult:
    email: str
    status: str


def is_configured() -> bool:
    """True when the BetterContact paid finder is configured (an API key is set)."""
    from openoutreach.core.models import SiteConfig

    return bool(SiteConfig.load().bettercontact_api_key)


def resolve_email(query: BetterContactQuery) -> BetterContactResult | None:
    """Resolve one lead's work email via BetterContact.

    Returns the result on a hit, None on a genuine miss (it ran, found
    nothing). Raises BetterContactUnavailable when no key is set or the service
    is unreachable — the caller treats that differently from a real miss.
    """
    from openoutreach.core.models import SiteConfig

    api_key = SiteConfig.load().bettercontact_api_key
    if not api_key:
        raise BetterContactUnavailable("no BetterContact API key configured")

    logger.info("bettercontact: looking up work email for %s …", query.linkedin_url)
    result = find_email(api_key, query)
    if result:
        logger.info("bettercontact: resolved %s for %s", result.email, query.linkedin_url)
    else:
        logger.info("bettercontact: no email for %s", query.linkedin_url)
    return result


def find_email(api_key: str, query: BetterContactQuery) -> BetterContactResult | None:
    """Submit one lead, poll until done, return its email — None on a genuine
    miss (it ran, found nothing). Raises BetterContactUnavailable on a transport
    failure (HTTP error, network drop, poll timeout) or an empty submit."""
    payload = {
        "data": [{
            "first_name": query.first_name,
            "last_name": query.last_name,
            "company": query.company,
            "company_domain": query.company_domain,
            "linkedin_url": query.linkedin_url,
        }],
        "enrich_email_address": True,
        "enrich_phone_number": False,
    }
    body = submit_and_poll(api_key, _ENRICH_URL, payload)
    rows = body.get("data") or []
    return _row_to_result(rows[0]) if rows else None


# ── shared async transport (used by enrichment + Lead Finder discovery) ───


def submit_and_poll(api_key: str, url: str, body: dict) -> dict:
    """Submit one job to a BetterContact async endpoint, poll until terminated,
    return the terminal JSON body.

    The two BetterContact endpoints — enrichment (`/async`) and Lead Finder
    (`/lead_finder/async`) — share this submit→poll contract; only their request
    body and the key holding the results (`data` vs `leads`) differ, so callers
    pull those out themselves. Raises BetterContactUnavailable on a transport
    failure (HTTP error, network drop, poll timeout) or an empty submit.
    """
    with _session(api_key) as session:
        try:
            request_id = _submit(session, url, body)
            logger.info("bettercontact: submitted (req %s), polling every %ds (up to %ds) …",
                        request_id, _POLL_INTERVAL_S, _POLL_TIMEOUT_S)
            return _poll(session, url, request_id)
        except (requests.RequestException, TimeoutError) as exc:
            raise BetterContactUnavailable(f"BetterContact unreachable: {exc}") from exc


def _session(api_key: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({"X-API-Key": api_key, "User-Agent": _BROWSER_UA})
    return session


def _submit(session: requests.Session, url: str, body: dict) -> str:
    resp = session.post(url, json=body, timeout=_HTTP_TIMEOUT_S)
    resp.raise_for_status()
    payload = resp.json()
    request_id = payload.get("request_id") or payload.get("id")
    if not request_id:
        raise BetterContactUnavailable("BetterContact returned no request id")
    return request_id


def _poll(session: requests.Session, url: str, request_id: str) -> dict:
    """Poll until status is terminal; return the terminal JSON body."""
    deadline = time.monotonic() + _POLL_TIMEOUT_S
    attempt = 0
    while True:
        resp = session.get(f"{url}/{request_id}", timeout=_HTTP_TIMEOUT_S)
        resp.raise_for_status()
        body = resp.json()
        if body.get("status") == "terminated":
            return body
        attempt += 1
        logger.debug("bettercontact: poll %d for %s — status=%s", attempt, request_id, body.get("status"))
        if time.monotonic() >= deadline:
            raise TimeoutError(f"poll timed out for {request_id}")
        time.sleep(_POLL_INTERVAL_S)


def _row_to_result(row: dict) -> BetterContactResult | None:
    email = row.get("contact_email_address")
    status = row.get("contact_email_address_status")
    if email and status in _USABLE_STATUSES:
        return BetterContactResult(email=email, status=status)
    return None
