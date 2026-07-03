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


def create_lead(row: dict, country_code: str = "") -> bool:
    """Persist one Lead Finder row as an embedded Lead awaiting qualification.

    Keyed on ``profile_url`` (the provider's per-person URL). Stamps the
    embedding and the firmographic ``profile_text`` from the same row fields, so
    qualification never re-fetches anything. ``country_code`` is the ICP's target
    country (Lead Finder rows carry no ISO code) — blank means unknown, which the
    contacts-store geo-gate treats conservatively. Returns True when a new Lead
    was created, False when one already existed (idempotent re-discovery).
    """
    from openoutreach.crm.models import Lead
    from openoutreach.discovery import embed_row, profile_text_for

    profile_url = row.get("contact_linkedin_profile_url")
    if not profile_url:
        return False

    _, created = Lead.objects.get_or_create(
        profile_url=profile_url,
        defaults={
            "embedding": np.asarray(embed_row(row), dtype=np.float32).tobytes(),
            "profile_text": profile_text_for(row),
            "country_code": country_code,
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
