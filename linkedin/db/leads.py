import json
import logging
from typing import Dict, Any, Optional

from django.db import transaction

from linkedin.db.urls import url_to_public_id, public_id_to_url
from linkedin.enums import ProfileState

logger = logging.getLogger(__name__)


def _lead_profile(lead) -> Optional[dict]:
    """Return the parsed profile dict stored as description on the Lead."""
    if not lead.description:
        return None
    try:
        return json.loads(lead.description)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse profile JSON for lead pk=%s", lead.pk)
        return None


def resolve_urn(public_id: str, session=None) -> Optional[str]:
    """Return the LinkedIn URN for a public identifier.

    Reads from the stored profile JSON. If the lead isn't enriched yet and a
    session is provided, calls ensure_lead_enriched first (lazy Voyager fetch).
    """
    from crm.models import Lead

    clean_url = public_id_to_url(public_id)
    lead = Lead.objects.filter(linkedin_url=clean_url).first()
    if not lead:
        return None

    if not lead.description and session:
        from linkedin.db.enrichment import ensure_lead_enriched
        ensure_lead_enriched(session, lead.pk, public_id)
        lead.refresh_from_db(fields=["description"])

    profile = _lead_profile(lead)
    return profile.get("urn") if profile else None


def lead_to_profile_dict(lead) -> dict | None:
    """Convert a Lead to the standard profile dict shape used by qualifiers and pools.

    Returns None if the lead has no public_identifier.
    """
    profile = _lead_profile(lead) or {}
    public_id = lead.public_identifier
    if not public_id:
        public_id = url_to_public_id(lead.linkedin_url) if lead.linkedin_url else ""
    if not public_id:
        return None
    return {
        "lead_id": lead.pk,
        "public_identifier": public_id,
        "url": lead.linkedin_url or "",
        "profile": profile,
        "meta": {},
    }


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

    Returns leads that are not permanently disqualified and have no Deal in
    this campaign. A lead rejected by another campaign (FAILED Deal in a
    different campaign) is still eligible here.
    """
    from crm.models import Lead

    leads = Lead.objects.filter(
        disqualified=False,
    ).exclude(
        deal__campaign=session.campaign,
    )

    result = []
    for lead in leads:
        d = lead_to_profile_dict(lead)
        if d:
            result.append(d)
    return result


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


def lead_profile_by_id(lead_id: int) -> Optional[dict]:
    """Load and parse the profile JSON stored on a Lead, looked up by PK."""
    from crm.models import Lead

    lead = Lead.objects.filter(pk=lead_id).first()
    if not lead:
        return None
    return _lead_profile(lead)


def _update_lead_fields(lead, profile: Dict[str, Any]):
    """Update Lead model fields from parsed LinkedIn profile."""
    lead.first_name = profile.get("first_name", "") or ""
    lead.last_name = profile.get("last_name", "") or ""

    positions = profile.get("positions", [])
    if positions:
        lead.company_name = positions[0].get("company_name", "") or ""

    lead.description = json.dumps(profile, ensure_ascii=False, default=str)
    lead.save()
