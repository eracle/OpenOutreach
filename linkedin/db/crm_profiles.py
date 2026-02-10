# linkedin/db/crm_profiles.py
"""
Profile CRUD backed by DjangoCRM models (Lead, Contact, Company, Deal).

Same public API as the old SQLAlchemy-based profiles.py, but using Django ORM.
"""
import json
import logging
import uuid
from datetime import date
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse, unquote

import pandas as pd
from django.core.files.base import ContentFile
from django.utils import timezone
from termcolor import colored

from linkedin.navigation.enums import ProfileState

logger = logging.getLogger(__name__)

# Maps ProfileState enum values to Stage names in the CRM.
STATE_TO_STAGE = {
    ProfileState.DISCOVERED: "Discovered",
    ProfileState.ENRICHED: "Enriched",
    ProfileState.PENDING: "Pending",
    ProfileState.CONNECTED: "Connected",
    ProfileState.COMPLETED: "Completed",
    ProfileState.FAILED: "Failed",
}


class ProfileRow:
    """
    Lightweight wrapper that exposes .state and .profile attributes,
    compatible with the campaign engine's access patterns.
    """

    def __init__(self, lead, deal, contact=None, company=None):
        self.lead = lead
        self.deal = deal
        self.contact = contact
        self.company = company

    @property
    def state(self) -> str:
        if not self.deal or not self.deal.stage:
            return ProfileState.DISCOVERED.value
        stage_name = self.deal.stage.name
        # Reverse lookup: stage name → ProfileState value
        for ps, sname in STATE_TO_STAGE.items():
            if sname == stage_name:
                return ps.value
        return ProfileState.DISCOVERED.value

    @property
    def profile(self) -> Optional[dict]:
        """Return the parsed profile dict stored as description on the Lead."""
        if not self.lead.description:
            return None
        try:
            return json.loads(self.lead.description)
        except (json.JSONDecodeError, TypeError):
            return None

    @property
    def public_identifier(self) -> str:
        return url_to_public_id(self.lead.website) if self.lead.website else ""


def _make_ticket() -> str:
    """Generate a unique 16-char ticket for a Deal."""
    return uuid.uuid4().hex[:16]


def _get_department():
    from common.models import Department
    return Department.objects.get(name="LinkedIn Outreach")


def _get_stage(state: ProfileState):
    from crm.models import Stage
    dept = _get_department()
    stage_name = STATE_TO_STAGE[state]
    return Stage.objects.get(name=stage_name, department=dept)


def _get_lead_source():
    from crm.models import LeadSource
    dept = _get_department()
    return LeadSource.objects.get(name="LinkedIn Scraper", department=dept)


def add_profile_urls(session: "AccountSession", urls: List[str]):
    """
    For each URL, create a Lead + Deal in 'Discovered' stage.
    Skips URLs that already have a Lead.
    """
    from crm.models import Lead, Deal

    if not urls:
        return

    dept = _get_department()
    stage = _get_stage(ProfileState.DISCOVERED)
    lead_source = _get_lead_source()
    owner = session.django_user

    count = 0
    for url in urls:
        try:
            pid = url_to_public_id(url)
        except ValueError:
            continue

        clean_url = public_id_to_url(pid)

        # Check if Lead already exists for this URL
        if Lead.objects.filter(website=clean_url).exists():
            continue

        lead = Lead.objects.create(
            website=clean_url,
            owner=owner,
            department=dept,
            lead_source=lead_source,
        )

        Deal.objects.create(
            name=f"LinkedIn: {pid}",
            lead=lead,
            stage=stage,
            owner=owner,
            department=dept,
            next_step="",
            next_step_date=date.today(),
            ticket=_make_ticket(),
        )
        count += 1

    logger.debug("Discovered %d unique LinkedIn profiles", count)


def save_scraped_profile(
    session: "AccountSession",
    url: str,
    profile: Dict[str, Any],
    data: Optional[Dict[str, Any]] = None,
):
    """
    Update Lead fields from parsed profile, create/update Contact + Company,
    move Deal to 'Enriched' stage, attach raw data as TheFile.
    """
    from crm.models import Lead, Deal, Contact, Company
    from common.models import TheFile

    public_id = url_to_public_id(url)
    clean_url = public_id_to_url(public_id)

    lead = Lead.objects.filter(website=clean_url).first()
    if lead is None:
        # Auto-create if missing
        dept = _get_department()
        stage = _get_stage(ProfileState.DISCOVERED)
        lead_source = _get_lead_source()
        owner = session.django_user

        lead = Lead.objects.create(
            website=clean_url,
            owner=owner,
            department=dept,
            lead_source=lead_source,
        )
        Deal.objects.create(
            name=f"LinkedIn: {public_id}",
            lead=lead,
            stage=stage,
            owner=owner,
            department=dept,
            next_step="",
            next_step_date=date.today(),
            ticket=_make_ticket(),
        )

    # Update Lead from parsed profile
    lead.first_name = profile.get("first_name", "") or ""
    lead.last_name = profile.get("last_name", "") or ""
    lead.title = profile.get("headline", "") or ""
    lead.city_name = profile.get("location_name", "") or ""

    # Store phone/email if available
    if profile.get("email"):
        lead.email = profile["email"]
    if profile.get("phone"):
        lead.phone = profile["phone"]

    # Store company name from first position
    positions = profile.get("positions", [])
    if positions:
        lead.company_name = positions[0].get("company_name", "") or ""

    # Store full parsed profile as JSON in description
    lead.description = json.dumps(profile, ensure_ascii=False, default=str)
    lead.save()

    # Create/update Company from first position
    company = None
    if positions and positions[0].get("company_name"):
        company_name = positions[0]["company_name"]
        company, _ = Company.objects.get_or_create(
            full_name=company_name,
            defaults={
                "owner": lead.owner,
                "department": lead.department,
            },
        )
        if company_name and not company.website:
            # Don't overwrite if company already has a website
            pass
        lead.company = company

    # Create/update Contact
    if lead.first_name:
        contact_kwargs = {
            "first_name": lead.first_name,
            "last_name": lead.last_name or "",
        }
        if company:
            contact_kwargs["company"] = company

        contact = None
        if company:
            contact = Contact.objects.filter(
                first_name=lead.first_name,
                last_name=lead.last_name or "",
                company=company,
            ).first()

        if contact is None:
            contact = Contact.objects.create(
                **contact_kwargs,
                title=lead.title or "",
                owner=lead.owner,
                department=lead.department,
            )

        lead.contact = contact
        lead.save()

    # Move Deal to Enriched stage
    deal = Deal.objects.filter(lead=lead).first()
    if deal:
        enriched_stage = _get_stage(ProfileState.ENRICHED)
        deal.stage = enriched_stage
        deal.change_stage_data(date.today())
        if company:
            deal.company = company
        if lead.contact:
            deal.contact = lead.contact
        deal.save()

    # Attach raw Voyager JSON as TheFile
    if data:
        from django.contrib.contenttypes.models import ContentType
        ct = ContentType.objects.get_for_model(lead)
        raw_json = json.dumps(data, ensure_ascii=False, default=str)
        the_file = TheFile(
            content_type=ct,
            object_id=lead.pk,
        )
        the_file.file.save(
            f"{public_id}_voyager.json",
            ContentFile(raw_json.encode("utf-8")),
            save=True,
        )

    debug_profile_preview(profile) if logger.isEnabledFor(logging.DEBUG) else None
    logger.debug("SUCCESS: Saved enriched profile → %s", public_id)


def set_profile_state(session: "AccountSession", public_identifier: str, new_state: str):
    """
    Move the Deal linked to this Lead to the corresponding Stage.
    """
    from crm.models import Lead, Deal, ClosingReason

    clean_url = public_id_to_url(public_identifier)
    lead = Lead.objects.filter(website=clean_url).first()

    if not lead:
        # Auto-create
        dept = _get_department()
        stage = _get_stage(ProfileState.DISCOVERED)
        lead_source = _get_lead_source()
        owner = session.django_user

        lead = Lead.objects.create(
            website=clean_url,
            owner=owner,
            department=dept,
            lead_source=lead_source,
        )
        Deal.objects.create(
            name=f"LinkedIn: {public_identifier}",
            lead=lead,
            stage=stage,
            owner=owner,
            department=dept,
            next_step="",
            next_step_date=date.today(),
            ticket=_make_ticket(),
        )

    deal = Deal.objects.filter(lead=lead).first()
    if not deal:
        return

    ps = ProfileState(new_state)
    new_stage = _get_stage(ps)
    deal.stage = new_stage
    deal.change_stage_data(date.today())

    dept = _get_department()

    if ps == ProfileState.FAILED:
        reason = ClosingReason.objects.filter(
            name="Failed", department=dept
        ).first()
        if reason:
            deal.closing_reason = reason
        deal.active = False

    if ps == ProfileState.COMPLETED:
        reason = ClosingReason.objects.filter(
            name="Completed", department=dept
        ).first()
        if reason:
            deal.closing_reason = reason
        deal.win_closing_date = timezone.now()

    deal.save()

    # Log state change with colors
    log_msg = None
    match new_state:
        case ProfileState.DISCOVERED:
            log_msg = colored("DISCOVERED", "green")
        case ProfileState.ENRICHED:
            log_msg = colored("ENRICHED", "yellow", attrs=["bold"])
        case ProfileState.PENDING:
            log_msg = colored("PENDING", "yellow", attrs=["bold"])
        case ProfileState.CONNECTED:
            log_msg = colored("CONNECTED", "green")
        case ProfileState.COMPLETED:
            log_msg = colored("COMPLETED", "green", attrs=["bold"])
        case _:
            log_msg = colored("ERROR", "red", attrs=["bold"])

    logger.info("%s %s", public_identifier, log_msg)


def get_profile(session: "AccountSession", public_identifier: str) -> Optional[ProfileRow]:
    """
    Query Lead + related Contact/Company/Deal.
    Returns a ProfileRow compatible with campaign engine access patterns.
    """
    from crm.models import Lead, Deal

    clean_url = public_id_to_url(public_identifier)
    lead = Lead.objects.filter(website=clean_url).first()
    if not lead:
        return None

    deal = Deal.objects.filter(lead=lead).first()
    contact = lead.contact if hasattr(lead, 'contact') else None
    company = lead.company if hasattr(lead, 'company') else None

    return ProfileRow(lead=lead, deal=deal, contact=contact, company=company)


def get_profile_from_url(session: "AccountSession", url: str) -> Optional[ProfileRow]:
    public_identifier = url_to_public_id(url)
    return get_profile(session, public_identifier)


def get_next_url_to_scrape(session: "AccountSession", limit: int = 1) -> List[str]:
    """Query Deals in 'Discovered' stage, return Lead URLs."""
    from crm.models import Deal

    stage = _get_stage(ProfileState.DISCOVERED)
    deals = Deal.objects.filter(
        stage=stage,
        owner=session.django_user,
    ).select_related("lead")[:limit]

    return [deal.lead.website for deal in deals if deal.lead and deal.lead.website]


def count_pending_scrape(session: "AccountSession") -> int:
    """Count Deals in 'Discovered' stage."""
    from crm.models import Deal

    stage = _get_stage(ProfileState.DISCOVERED)
    return Deal.objects.filter(
        stage=stage,
        owner=session.django_user,
    ).count()


def get_updated_at_df(session: "AccountSession", public_identifiers: List[str]) -> pd.DataFrame:
    """
    Return a DataFrame with public_identifier and updated_at for existing Leads.
    """
    from crm.models import Lead

    if not public_identifiers:
        return pd.DataFrame(columns=["public_identifier", "updated_at"])

    # Build URL list from public_identifiers
    urls = [public_id_to_url(pid) for pid in public_identifiers]

    results = Lead.objects.filter(
        website__in=urls,
    ).values_list("website", "update_date")

    if not results:
        return pd.DataFrame(columns=["public_identifier", "updated_at"])

    rows = [
        {"public_identifier": url_to_public_id(url), "updated_at": updated}
        for url, updated in results
    ]

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["public_identifier", "updated_at"])

    logger.debug("Retrieved updated_at for %d profiles from DB", len(df))
    return df


# ── Pure URL helpers (no DB dependency) ──

def url_to_public_id(url: str) -> str:
    """
    Strict LinkedIn public ID extractor:
    - Path MUST start with /in/
    - Returns the second segment, percent-decoded
    - Anything else → raises ValueError
    """
    if not url:
        raise ValueError("Empty URL")

    path = urlparse(url.strip()).path
    parts = path.strip("/").split("/")

    if len(parts) < 2 or parts[0] != "in":
        raise ValueError(f"Not a valid /in/ profile URL: {url!r}")

    public_id = parts[1]
    return unquote(public_id)


def public_id_to_url(public_id: str) -> str:
    """Convert public_identifier back to a clean LinkedIn profile URL."""
    if not public_id:
        return ""
    public_id = public_id.strip("/")
    return f"https://www.linkedin.com/in/{public_id}/"


def debug_profile_preview(enriched):
    pretty = json.dumps(enriched, indent=2, ensure_ascii=False, default=str)
    preview_lines = pretty.splitlines()[:3]
    logger.debug("=== ENRICHED PROFILE PREVIEW ===\n%s\n...", "\n".join(preview_lines))
