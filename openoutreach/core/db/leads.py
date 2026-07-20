import logging

import numpy as np
from django.db import transaction

from openoutreach.crm.models import DealState

logger = logging.getLogger(__name__)


@transaction.atomic
def promote_lead_to_deal(session, profile_url: str, reason: str = ""):
    """Create a QUALIFIED Deal for a Lead.

    Returns the Deal.
    """
    from openoutreach.crm.models import Lead, Deal

    lead = Lead.objects.filter(profile_url=profile_url).first()
    if not lead:
        raise ValueError(f"No Lead for {profile_url}")

    deal = Deal.objects.create(
        lead=lead,
        campaign=session.campaign,
        state=DealState.QUALIFIED,
        reason=reason,
    )

    from termcolor import colored
    logger.info("%s %s", profile_url, colored("QUALIFIED", "green", attrs=["bold"]))
    return deal


def create_lead(row: dict, country_code: str = "", discovered_by=None,
                query_terms: str = "") -> bool:
    """Persist one Lead Finder row as an embedded Lead awaiting qualification.

    Keyed on ``profile_url`` (the provider's per-person URL). Stamps the firmographic
    ``profile_text`` (the LLM qualifier's input) and the embedding from the same row,
    so qualification never re-fetches anything. ``country_code`` is the ICP's target
    country (Lead Finder rows carry no ISO code) — blank means unknown, which the
    contacts-store geo-gate treats conservatively.

    ``discovered_by`` is the query node that surfaced this row; it lands only on first
    touch (a profile another query already created keeps its original node). Its
    ``query_terms`` are folded into the **embedding only** — not ``profile_text`` — so
    the GP learns which query keywords surface good leads while the LLM judges the
    person on firmographics alone. Returns True when a new Lead was created, False when
    one already existed (idempotent re-discovery).
    """
    from openoutreach.crm.models import Lead
    from openoutreach.discovery import embed_profile, profile_text_for

    profile_url = row.get("contact_linkedin_profile_url")
    if not profile_url:
        return False

    profile_text = profile_text_for(row)
    _, created = Lead.objects.get_or_create(
        profile_url=profile_url,
        defaults={
            "embedding": np.asarray(
                embed_profile(profile_text, query_terms), dtype=np.float32).tobytes(),
            "profile_text": profile_text,
            "country_code": country_code,
            "discovered_by": discovered_by,
        },
    )
    return created


def disqualify_lead(profile_url: str):
    """Set Lead.disqualified = True (account-level, permanent, cross-campaign)."""
    from openoutreach.crm.models import Lead

    lead = Lead.objects.filter(profile_url=profile_url).first()
    if not lead:
        logger.warning("disqualify_lead: no Lead for %s", profile_url)
        return
    lead.disqualified = True
    lead.save(update_fields=["disqualified"])
