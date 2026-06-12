# openoutreach/emails/finder.py
"""Resolve a work email for a qualified lead, on demand.

`resolve_email` is the public entry point; BetterContact is the one provider
(see bettercontact.py). Called lazily when a lead needs an email — it submits
the lookup and waits for the result. A missing API key or a miss yields None,
never an error, so enrichment can't take down the daemon.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class FinderUnavailable(Exception):
    """The finder could not run — no API key configured, or the service was
    unreachable. Distinct from a genuine miss (finder ran, found no email)."""


@dataclass(frozen=True)
class FinderQuery:
    """A lead to resolve. linkedin_url alone works; name/company lift the hit rate."""

    linkedin_url: str
    first_name: str = ""
    last_name: str = ""
    company: str = ""
    company_domain: str = ""


@dataclass(frozen=True)
class FinderResult:
    email: str
    status: str


def resolve_email(query: FinderQuery) -> FinderResult | None:
    """Resolve one lead's work email.

    Returns the result on a hit, None on a genuine miss (finder ran, found
    nothing). Raises FinderUnavailable when no key is set or the service is
    unreachable — the caller treats that differently from a real miss.
    """
    from openoutreach.core.models import SiteConfig
    from openoutreach.emails import bettercontact

    api_key = SiteConfig.load().finder_api_key
    if not api_key:
        raise FinderUnavailable("no finder API key configured")
    return bettercontact.find_email(api_key, query)
