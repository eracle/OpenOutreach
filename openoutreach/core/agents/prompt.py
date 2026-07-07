# openoutreach/core/agents/prompt.py
"""Shared prompt generator for the outreach agents.

Both entrypoints — the LinkedIn follow-up agent and the email opener — render
from one Jinja base (``_outreach_base.j2``: identity, product docs, lead summary,
Mom Test strategy, shared rules) and fill only their channel-specific blocks. The
base context here is the shared half; each entrypoint adds its own extras.
"""
from __future__ import annotations

import jinja2

from openoutreach.core.conf import PROMPTS_DIR

_ENV = jinja2.Environment(loader=jinja2.FileSystemLoader(str(PROMPTS_DIR)))


def render(template_name: str, **context) -> str:
    """Render a prompt template by name from the shared prompts dir."""
    return _ENV.get_template(template_name).render(**context)


def base_context(session, deal) -> dict:
    """The channel-agnostic prompt variables shared by every outreach entrypoint."""
    campaign = deal.campaign
    self_prof = session.self_profile
    self_name = (
        f"{self_prof.get('first_name', '')} {self_prof.get('last_name', '')}".strip()
        or session.django_user.username
    )
    return {
        "self_name": self_name,
        "product_docs": campaign.product_docs or "",
        "campaign_target": campaign.campaign_target or "",
        "booking_link": campaign.booking_link or "",
        "profile_summary": _format_facts(deal.profile_summary),
    }


def _format_facts(summary: dict | None) -> str:
    """Render a `{facts: [...]}` summary blob as a bullet list."""
    facts = (summary or {}).get("facts") or []
    if not facts:
        return "(none yet)"
    return "\n".join(f"- {f}" for f in facts)
