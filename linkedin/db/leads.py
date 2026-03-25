import logging
import time
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
    return Lead.objects.filter(public_identifier=pid).exists()


def create_enriched_lead(session, url: str, profile: Dict[str, Any]) -> Optional[int]:
    """Create Lead with full profile data and embedding.

    Returns lead PK or None if exists.
    Does NOT create Deal — that comes at qualification.
    """
    from crm.models import Lead

    # Use canonical public_identifier from Voyager response when available.
    canonical_pid = profile.get("public_identifier")
    public_id = canonical_pid or url_to_public_id(url)
    clean_url = public_id_to_url(public_id)

    with transaction.atomic():
        if Lead.objects.filter(public_identifier=public_id).exists():
            return None
        lead = Lead.objects.create(linkedin_url=clean_url, public_identifier=public_id)
        _update_lead_fields(lead, profile)

    lead.get_embedding(session)

    logger.debug("Created enriched lead for %s (pk=%d)", public_id, lead.pk)
    return lead.pk


@transaction.atomic
def promote_lead_to_deal(session, public_id: str, reason: str = ""):
    """Create a QUALIFIED Deal for a Lead.

    Returns the Deal.
    """
    from crm.models import Lead, Deal

    lead = Lead.objects.filter(public_identifier=public_id).first()
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

    return [lead.to_profile_dict() for lead in leads]


def disqualify_lead(public_id: str):
    """Set Lead.disqualified = True (account-level, permanent, cross-campaign)."""
    from crm.models import Lead

    lead = Lead.objects.filter(public_identifier=public_id).first()
    if not lead:
        logger.warning("disqualify_lead: no Lead for %s", public_id)
        return
    lead.disqualified = True
    lead.save(update_fields=["disqualified"])


def discover_and_enrich(session, urls: set):
    """For each new URL, call Voyager API, create enriched Lead (with embedding).

    Skips URLs that already have a Lead. Rate-limits with enrich_min_interval.
    """
    from linkedin.api.client import PlaywrightLinkedinAPI
    from linkedin.conf import CAMPAIGN_CONFIG

    new_urls = [u for u in urls if not lead_exists(u)]
    if not new_urls:
        return

    logger.info("Discovered %d new profiles (%d total on page)", len(new_urls), len(urls))

    min_interval = CAMPAIGN_CONFIG.get("enrich_min_interval", 1)
    session.ensure_browser()
    api = PlaywrightLinkedinAPI(session=session)
    enriched = 0

    for url in new_urls:
        public_id = url_to_public_id(url)
        if not public_id:
            continue

        try:
            profile, _raw = api.get_profile(profile_url=url)
        except Exception:
            logger.warning("Voyager API failed for %s — skipping", url)
            continue

        if not profile:
            logger.warning("Empty profile for %s — skipping", url)
            continue

        if create_enriched_lead(session, url, profile) is not None:
            enriched += 1

        time.sleep(min_interval)

    logger.info("Enriched %d/%d new profiles", enriched, len(new_urls))


def _update_lead_fields(lead, profile: Dict[str, Any]):
    """Update Lead model fields from parsed LinkedIn profile."""
    lead.first_name = profile.get("first_name", "") or ""
    lead.last_name = profile.get("last_name", "") or ""

    positions = profile.get("positions", [])
    if positions:
        lead.company_name = positions[0].get("company_name", "") or ""

    lead.profile_data = profile
    lead.save()
