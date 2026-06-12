# openoutreach/emails/bettercontact.py
"""BetterContact email lookup — a single-key managed waterfall over an async
submit→poll HTTP contract. `find_email` is the only public function."""
from __future__ import annotations

import logging
import time

import requests

from openoutreach.emails.finder import FinderQuery, FinderResult, FinderUnavailable

logger = logging.getLogger(__name__)

_BASE = "https://app.bettercontact.rocks/api/v2/async"
_POLL_INTERVAL_S = 5
_POLL_TIMEOUT_S = 300
_HTTP_TIMEOUT_S = 30
_USABLE_STATUSES = frozenset({"valid", "deliverable", "catch_all_safe"})

# Cloudflare 403s a non-browser User-Agent (error 1010), so spoof a browser.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def find_email(api_key: str, query: FinderQuery) -> FinderResult | None:
    """Submit one lead, poll until done, return its email — None on a genuine
    miss (finder ran, found nothing). Raises FinderUnavailable on a transport
    failure (HTTP error, network drop, poll timeout) or an empty submit."""
    with _session(api_key) as session:
        try:
            request_id = _submit(session, query)
            row = _poll(session, request_id) if request_id else None
        except (requests.RequestException, TimeoutError) as exc:
            raise FinderUnavailable(f"BetterContact unreachable: {exc}") from exc
    if request_id is None:
        raise FinderUnavailable("BetterContact returned no request id")
    return _row_to_result(row) if row else None


def _session(api_key: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({"X-API-Key": api_key, "User-Agent": _BROWSER_UA})
    return session


def _submit(session: requests.Session, query: FinderQuery) -> str | None:
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
    resp = session.post(_BASE, json=payload, timeout=_HTTP_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json().get("id")


def _poll(session: requests.Session, request_id: str) -> dict | None:
    """Poll until status is terminal; return the lead's `data` row, or None."""
    deadline = time.monotonic() + _POLL_TIMEOUT_S
    while True:
        resp = session.get(f"{_BASE}/{request_id}", timeout=_HTTP_TIMEOUT_S)
        resp.raise_for_status()
        body = resp.json()
        if body.get("status") == "terminated":
            data = body.get("data", [])
            return data[0] if data else None
        if time.monotonic() >= deadline:
            raise TimeoutError(f"poll timed out for {request_id}")
        time.sleep(_POLL_INTERVAL_S)


def _row_to_result(row: dict) -> FinderResult | None:
    email = row.get("contact_email_address")
    status = row.get("contact_email_address_status")
    if email and status in _USABLE_STATUSES:
        return FinderResult(email=email, status=status)
    return None
