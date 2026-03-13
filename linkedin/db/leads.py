import json
import logging
from typing import Dict, Any, Optional

from django.core.files.base import ContentFile
from django.db import transaction
from termcolor import colored

from linkedin.db._helpers import _make_ticket, _get_stage, _get_lead_source
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
        return None


def lead_exists(url: str) -> bool:
    """Check if Lead already exists for this LinkedIn URL."""
    from crm.models import Lead

    pid = url_to_public_id(url)
    if not pid:
        return False
    clean_url = public_id_to_url(pid)
    return Lead.objects.filter(website=clean_url).exists()


@transaction.atomic
def create_enriched_lead(session, url: str, profile: Dict[str, Any], data: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """Create Lead with full profile data, Company, and embedding.

    Returns lead PK or None if exists.
    Does NOT create Contact or Deal — those come at qualification.
    """
    from crm.models import Lead
    from linkedin.ml.embeddings import embed_profile

    # Use canonical public_identifier from Voyager response when available.
    # The same person can appear under both an opaque URN URL and a vanity slug;
    # storing by canonical ID deduplicates naturally.
    canonical_pid = profile.get("public_identifier")
    public_id = canonical_pid or url_to_public_id(url)
    clean_url = public_id_to_url(public_id)

    if Lead.objects.filter(website=clean_url).exists():
        return None

    lead = Lead.objects.create(
        website=clean_url,
        owner=session.django_user,
        department=session.campaign.department,
        lead_source=_get_lead_source(session),
    )

    _update_lead_fields(lead, profile)
    _ensure_company(lead, profile)

    if data:
        _attach_raw_data(lead, public_id, data)

    embed_profile(lead.pk, public_id, profile)

    logger.debug("Created enriched lead for %s (pk=%d)", public_id, lead.pk)
    return lead.pk



@transaction.atomic
def promote_lead_to_contact(session, public_id: str):
    """Create Contact from Lead + Deal at 'Qualified' stage.

    Returns (contact, deal). Raises ValueError if Lead has no Company.
    """
    from crm.models import Lead, Contact, Deal
    from datetime import date

    clean_url = public_id_to_url(public_id)
    lead = Lead.objects.filter(website=clean_url).first()
    if not lead:
        raise ValueError(f"No Lead for {public_id}")

    company = lead.company
    if not company:
        raise ValueError(f"Lead {public_id} has no Company — cannot create Contact")

    # Create or get Contact
    contact = Contact.objects.filter(
        first_name=lead.first_name or "",
        last_name=lead.last_name or "",
        company=company,
    ).first()

    if contact is None:
        contact = Contact.objects.create(
            first_name=lead.first_name or "",
            last_name=lead.last_name or "",
            company=company,
            title=lead.title or "",
            owner=lead.owner,
            department=lead.department,
        )

    lead.contact = contact
    lead.save()

    dept = session.campaign.department

    # Create Deal at "Qualified" stage
    deal = Deal.objects.create(
        name=f"LinkedIn: {public_id}",
        lead=lead,
        contact=contact,
        company=company,
        stage=_get_stage(ProfileState.QUALIFIED, session),
        owner=session.django_user,
        department=dept,
        next_step="",
        next_step_date=date.today(),
        ticket=_make_ticket(),
    )

    logger.info("%s %s", public_id, colored("QUALIFIED", "green", attrs=["bold"]))
    return contact, deal


def get_leads_for_qualification(session) -> list:
    """Leads eligible for qualification in the current campaign.

    Returns leads that are not self-profile (disqualified=False) and have no
    Deal in this campaign's department. A lead rejected by another campaign
    (FAILED Deal in a different department) is still eligible here.
    """
    from crm.models import Lead

    dept = session.campaign.department
    leads = Lead.objects.filter(
        owner=session.django_user,
        disqualified=False,  # excludes self-profile only (account-level)
    ).exclude(
        deal__department=dept,  # excludes leads already evaluated in this campaign
    )

    result = []
    for lead in leads:
        profile = _lead_profile(lead) or {}
        public_id = url_to_public_id(lead.website) if lead.website else ""
        result.append({
            "public_identifier": public_id,
            "url": lead.website or "",
            "profile": profile,
            "lead_id": lead.pk,
        })
    return result



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
    lead.title = profile.get("headline", "") or ""
    lead.city_name = profile.get("location_name", "") or ""

    if profile.get("email"):
        lead.email = profile["email"]
    if profile.get("phone"):
        lead.phone = profile["phone"]

    positions = profile.get("positions", [])
    if positions:
        lead.company_name = positions[0].get("company_name", "") or ""

    lead.description = json.dumps(profile, ensure_ascii=False, default=str)
    lead.save()


def _ensure_company(lead, profile: Dict[str, Any]):
    """Create or get Company from first position. Returns Company or None."""
    from crm.models import Company

    positions = profile.get("positions", [])
    if not positions or not positions[0].get("company_name"):
        return None

    company, _ = Company.objects.get_or_create(
        full_name=positions[0]["company_name"],
        defaults={"owner": lead.owner, "department": lead.department},
    )
    lead.company = company
    lead.save()
    return company


def _attach_raw_data(lead, public_id: str, data: Dict[str, Any]):
    """Save raw Voyager JSON as TheFile attached to the Lead."""
    from common.models import TheFile
    from django.contrib.contenttypes.models import ContentType

    ct = ContentType.objects.get_for_model(lead)
    raw_json = json.dumps(data, ensure_ascii=False, default=str)
    the_file = TheFile(content_type=ct, object_id=lead.pk)
    the_file.file.save(
        f"{public_id}_voyager.json",
        ContentFile(raw_json.encode("utf-8")),
        save=True,
    )
