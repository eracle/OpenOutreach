import json
import logging
from typing import Dict, Any, Optional

from django.db import transaction

from linkedin.db.urls import url_to_public_id, public_id_to_url
from linkedin.enums import ProfileState

logger = logging.getLogger(__name__)


def lead_exists(url: str) -> bool:
    """Check if Lead already exists for this LinkedIn URL."""
    from crm.models import Lead

    pid = url_to_public_id(url)
    if not pid:
        return False
    clean_url = public_id_to_url(pid)
    return Lead.objects.filter(linkedin_url=clean_url).exists()


@transaction.atomic
def create_enriched_lead(session, url: str, profile: Dict[str, Any]) -> Optional[int]:
    """Create Lead with full profile data and embedding.

    Returns lead PK or None if exists.
    Does NOT create Deal — that comes at qualification.
    """
    from crm.models import Lead
    from linkedin.ml.embeddings import embed_profile

    # Use canonical public_identifier from Voyager response when available.
    canonical_pid = profile.get("public_identifier")
    public_id = canonical_pid or url_to_public_id(url)
    clean_url = public_id_to_url(public_id)

    if Lead.objects.filter(linkedin_url=clean_url).exists():
        return None

    lead = Lead.objects.create(linkedin_url=clean_url, public_identifier=public_id)

    _update_lead_fields(lead, profile)

    embed_profile(lead.pk, public_id, profile)

    logger.debug("Created enriched lead for %s (pk=%d)", public_id, lead.pk)
    return lead.pk


@transaction.atomic
def promote_lead_to_deal(session, public_id: str, reason: str = ""):
    """Create a QUALIFIED Deal for a Lead.

    Returns the Deal.
    """
    from crm.models import Lead, Deal

    clean_url = public_id_to_url(public_id)
    lead = Lead.objects.filter(linkedin_url=clean_url).first()
    if not lead:
        raise ValueError(f"No Lead for {public_id}")

    if not lead.company_name:
        raise ValueError(f"Lead {public_id} has no company_name — cannot create Deal")

    deal = Deal.objects.create(
        lead=lead,
        campaign=session.campaign,
        state=ProfileState.QUALIFIED,
        reason=reason,
    )

    from termcolor import colored
    logger.info("%s %s", public_id, colored("QUALIFIED", "green", attrs=["bold"]))
    return deal


def get_leads_for_qualification(session) -> list:
    """Leads eligible for qualification in the current campaign.

    Returns profile dicts for leads that are not permanently disqualified
    and have no Deal in this campaign.
    """
    from crm.models import Lead

    leads = Lead.objects.filter(
        disqualified=False,
    ).exclude(
        deal__campaign=session.campaign,
    )

    return [d for lead in leads if (d := lead.to_profile_dict())]


def disqualify_lead(public_id: str):
    """Set Lead.disqualified = True (account-level, permanent, cross-campaign)."""
    from crm.models import Lead

    clean_url = public_id_to_url(public_id)
    lead = Lead.objects.filter(linkedin_url=clean_url).first()
    if not lead:
        logger.warning("disqualify_lead: no Lead for %s", public_id)
        return
    lead.disqualified = True
    lead.save(update_fields=["disqualified"])


def _update_lead_fields(lead, profile: Dict[str, Any]):
    """Update Lead model fields from parsed LinkedIn profile."""
    lead.first_name = profile.get("first_name", "") or ""
    lead.last_name = profile.get("last_name", "") or ""

    positions = profile.get("positions", [])
    if positions:
        lead.company_name = positions[0].get("company_name", "") or ""

    lead.description = json.dumps(profile, ensure_ascii=False, default=str)
    lead.save()
