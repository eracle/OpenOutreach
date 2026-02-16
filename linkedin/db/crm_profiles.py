# linkedin/db/crm_profiles.py
"""
Profile CRUD backed by DjangoCRM models (Lead, Contact, Company, Deal).

Same public API as the old SQLAlchemy-based profiles.py, but using Django ORM.
"""
import json
import logging
import uuid
from datetime import date, timedelta
from typing import Dict, Any, Optional, List
from urllib.parse import quote, urlparse, unquote

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from termcolor import colored

from linkedin.navigation.enums import ProfileState

logger = logging.getLogger(__name__)

# Maps ProfileState enum values to Stage names in the CRM.
STATE_TO_STAGE = {
    ProfileState.DISCOVERED: "Discovered",
    ProfileState.ENRICHED: "Enriched",
    ProfileState.QUALIFIED: "Qualified",
    ProfileState.DISQUALIFIED: "Disqualified",
    ProfileState.PENDING: "Pending",
    ProfileState.CONNECTED: "Connected",
    ProfileState.COMPLETED: "Completed",
    ProfileState.FAILED: "Failed",
    ProfileState.IGNORED: "Ignored",
}

# Reverse lookup: stage name → ProfileState value
_STAGE_TO_STATE = {v: k.value for k, v in STATE_TO_STAGE.items()}


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


def _get_or_create_lead_and_deal(session, public_id: str, clean_url: str):
    """
    Return (lead, deal) for the given profile URL, creating both if missing.
    """
    from crm.models import Lead, Deal

    lead = Lead.objects.filter(website=clean_url).first()
    if lead is None:
        lead = Lead.objects.create(
            website=clean_url,
            owner=session.django_user,
            department=_get_department(),
            lead_source=_get_lead_source(),
        )

    deal = Deal.objects.filter(lead=lead).first()
    if deal is None:
        deal = Deal.objects.create(
            name=f"LinkedIn: {public_id}",
            lead=lead,
            stage=_get_stage(ProfileState.DISCOVERED),
            owner=session.django_user,
            department=_get_department(),
            next_step="",
            next_step_date=date.today(),
            ticket=_make_ticket(),
        )

    return lead, deal


def _parse_next_step(deal) -> dict:
    """Parse deal.next_step as JSON, return empty dict on failure or empty string."""
    if not deal.next_step:
        return {}
    try:
        return json.loads(deal.next_step)
    except (json.JSONDecodeError, TypeError):
        return {}


def _set_next_step(deal, data: dict):
    """Serialize dict to JSON string and assign to deal.next_step."""
    deal.next_step = json.dumps(data)


def _lead_state(deal) -> str:
    """Derive ProfileState value from a Deal's stage name."""
    if not deal or not deal.stage:
        return ProfileState.DISCOVERED.value
    return _STAGE_TO_STATE.get(deal.stage.name, ProfileState.DISCOVERED.value)


def _lead_profile(lead) -> Optional[dict]:
    """Return the parsed profile dict stored as description on the Lead."""
    if not lead.description:
        return None
    try:
        return json.loads(lead.description)
    except (json.JSONDecodeError, TypeError):
        return None


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
    return company


def _ensure_contact(lead, company):
    """Create Contact if lead has a name and company. Requires company (NOT NULL on crm_contact)."""
    from crm.models import Contact

    if not lead.first_name or not company:
        return

    contact = Contact.objects.filter(
        first_name=lead.first_name,
        last_name=lead.last_name or "",
        company=company,
    ).first()

    if contact is None:
        contact = Contact.objects.create(
            first_name=lead.first_name,
            last_name=lead.last_name or "",
            company=company,
            title=lead.title or "",
            owner=lead.owner,
            department=lead.department,
        )

    lead.contact = contact
    lead.save()


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


@transaction.atomic
def save_scraped_profile(
    session: "AccountSession",
    url: str,
    profile: Dict[str, Any],
    data: Optional[Dict[str, Any]] = None,
):
    """
    Update Lead fields from parsed profile, create/update Contact + Company,
    attach raw data as TheFile.  Does NOT change Deal stage — caller must
    call set_profile_state() afterwards.
    """
    public_id = url_to_public_id(url)
    clean_url = public_id_to_url(public_id)

    lead, deal = _get_or_create_lead_and_deal(session, public_id, clean_url)

    _update_lead_fields(lead, profile)
    company = _ensure_company(lead, profile)
    _ensure_contact(lead, company)

    if company:
        deal.company = company
    if lead.contact:
        deal.contact = lead.contact
    deal.save()

    if data:
        _attach_raw_data(lead, public_id, data)

    logger.debug("Saved profile data for %s", public_id)


def set_profile_state(
    session: "AccountSession",
    public_identifier: str,
    new_state: str,
    reason: str = "",
):
    """
    Move the Deal linked to this Lead to the corresponding Stage.
    Optional *reason* is stored in deal.description (visible in Django Admin).
    """
    from crm.models import ClosingReason

    clean_url = public_id_to_url(public_identifier)
    _lead, deal = _get_or_create_lead_and_deal(session, public_identifier, clean_url)

    ps = ProfileState(new_state)
    old_stage_name = deal.stage.name if deal.stage else None
    new_stage = _get_stage(ps)
    state_changed = (old_stage_name != new_stage.name)

    old_is_pending = (old_stage_name == STATE_TO_STAGE[ProfileState.PENDING])
    new_is_pending = (ps == ProfileState.PENDING)

    deal.stage = new_stage
    deal.change_stage_data(date.today())
    deal.next_step_date = date.today()

    # Clear backoff metadata on any transition to or from PENDING
    if old_is_pending or new_is_pending:
        deal.next_step = ""

    if reason:
        deal.description = reason

    dept = _get_department()

    if ps == ProfileState.FAILED:
        closing = ClosingReason.objects.filter(
            name="Failed", department=dept
        ).first()
        if closing:
            deal.closing_reason = closing
        deal.active = False

    if ps == ProfileState.COMPLETED:
        closing = ClosingReason.objects.filter(
            name="Completed", department=dept
        ).first()
        if closing:
            deal.closing_reason = closing
        deal.win_closing_date = timezone.now()

    if ps == ProfileState.IGNORED:
        closing = ClosingReason.objects.filter(
            name="Ignored", department=dept
        ).first()
        if closing:
            deal.closing_reason = closing
        deal.active = False

    if ps == ProfileState.DISQUALIFIED:
        closing = ClosingReason.objects.filter(
            name="Disqualified", department=dept
        ).first()
        if closing:
            deal.closing_reason = closing
        deal.active = False

    deal.save()

    _STATE_LOG_STYLE = {
        ProfileState.DISCOVERED: ("DISCOVERED", "green", []),
        ProfileState.ENRICHED: ("ENRICHED", "yellow", ["bold"]),
        ProfileState.QUALIFIED: ("QUALIFIED", "green", ["bold"]),
        ProfileState.DISQUALIFIED: ("DISQUALIFIED", "red", ["bold"]),
        ProfileState.PENDING: ("PENDING", "cyan", []),
        ProfileState.CONNECTED: ("CONNECTED", "green", ["bold"]),
        ProfileState.COMPLETED: ("COMPLETED", "green", ["bold"]),
        ProfileState.FAILED: ("FAILED", "red", ["bold"]),
        ProfileState.IGNORED: ("IGNORED", "blue", ["bold"]),
    }
    label, color, attrs = _STATE_LOG_STYLE.get(ps, ("ERROR", "red", ["bold"]))
    suffix = f" ({reason})" if reason else ""
    if state_changed:
        logger.info("%s %s%s", public_identifier, colored(label, color, attrs=attrs), suffix)
    else:
        logger.debug("%s %s (unchanged)%s", public_identifier, label, suffix)


def get_profile(session: "AccountSession", public_identifier: str) -> Optional[dict]:
    """
    Query Lead + Deal and return a dict with 'state' and 'profile' keys,
    or None if the Lead doesn't exist.
    """
    from crm.models import Lead, Deal

    clean_url = public_id_to_url(public_identifier)
    lead = Lead.objects.filter(website=clean_url).first()
    if not lead:
        return None

    deal = Deal.objects.filter(lead=lead).first()

    return {
        "state": _lead_state(deal),
        "profile": _lead_profile(lead),
    }


def get_next_url_to_scrape(session: "AccountSession") -> List[str]:
    """Query Deals in 'Discovered' stage, return Lead URLs."""
    from crm.models import Deal

    stage = _get_stage(ProfileState.DISCOVERED)
    deals = Deal.objects.filter(
        stage=stage,
        owner=session.django_user,
    ).select_related("lead")

    return [deal.lead.website for deal in deals if deal.lead and deal.lead.website]


def count_pending_scrape(session: "AccountSession") -> int:
    """Count Deals in 'Discovered' stage."""
    from crm.models import Deal

    stage = _get_stage(ProfileState.DISCOVERED)
    return Deal.objects.filter(
        stage=stage,
        owner=session.django_user,
    ).count()


def get_updated_at_map(session: "AccountSession", public_identifiers: List[str]) -> dict:
    """
    Return a dict mapping public_identifier → update_date for existing Leads.
    """
    from crm.models import Lead

    if not public_identifiers:
        return {}

    urls = [public_id_to_url(pid) for pid in public_identifiers]

    results = Lead.objects.filter(
        website__in=urls,
    ).values_list("website", "update_date")

    result_map = {
        url_to_public_id(url): updated
        for url, updated in results
    }

    logger.debug("Retrieved updated_at for %d profiles from DB", len(result_map))
    return result_map


def _deal_to_profile_dict(deal) -> dict:
    """Convert a Deal (with select_related lead) to a profile dict for lanes."""
    lead = deal.lead
    profile = _lead_profile(lead) or {}
    public_id = url_to_public_id(lead.website) if lead.website else ""
    return {
        "public_identifier": public_id,
        "url": lead.website or "",
        "profile": profile,
        "meta": _parse_next_step(deal),
    }


def get_enriched_profiles(session) -> list:
    """All Deals at ENRICHED stage for this user."""
    from crm.models import Deal

    stage = _get_stage(ProfileState.ENRICHED)
    deals = Deal.objects.filter(
        stage=stage,
        owner=session.django_user,
    ).select_related("lead")

    return [_deal_to_profile_dict(d) for d in deals if d.lead and d.lead.website]


def count_enriched_profiles(session) -> int:
    """Count Deals at ENRICHED stage."""
    from crm.models import Deal

    stage = _get_stage(ProfileState.ENRICHED)
    return Deal.objects.filter(
        stage=stage,
        owner=session.django_user,
    ).count()


def get_qualified_profiles(session) -> list:
    """All Deals at QUALIFIED stage for this user."""
    from crm.models import Deal

    stage = _get_stage(ProfileState.QUALIFIED)
    deals = Deal.objects.filter(
        stage=stage,
        owner=session.django_user,
    ).select_related("lead")

    return [_deal_to_profile_dict(d) for d in deals if d.lead and d.lead.website]


def count_qualified_profiles(session) -> int:
    """Count Deals at QUALIFIED stage."""
    from crm.models import Deal

    stage = _get_stage(ProfileState.QUALIFIED)
    return Deal.objects.filter(
        stage=stage,
        owner=session.django_user,
    ).count()


def get_pending_profiles(session, recheck_after_hours: float) -> list:
    """PENDING deals filtered by per-profile exponential backoff.

    Each deal stores its own backoff in ``deal.next_step`` as
    ``{"backoff_hours": <float>}``.  If absent, *recheck_after_hours*
    is used as the default (first check).
    """
    from crm.models import Deal

    now = timezone.now()
    stage = _get_stage(ProfileState.PENDING)
    all_deals = list(
        Deal.objects.filter(
            stage=stage,
            owner=session.django_user,
        ).select_related("lead")
    )

    ready = []
    waiting = []
    for d in all_deals:
        meta = _parse_next_step(d)
        backoff = meta.get("backoff_hours", recheck_after_hours)
        cutoff = d.update_date + timedelta(hours=backoff)
        if now >= cutoff:
            ready.append(d)
        else:
            waiting.append((d, backoff, cutoff))

    # Sort waiting profiles by soonest next check.
    waiting.sort(key=lambda t: t[2])

    for d, backoff, cutoff in waiting:
        remaining = cutoff - now
        total_min = int(remaining.total_seconds() // 60)
        h, m = divmod(total_min, 60)
        slug = d.name.removeprefix("LinkedIn: ")
        logger.debug(
            "  ↳ %-30s  %3dh %02dm  (backoff %.0fh)",
            slug, h, m, backoff,
        )

    if waiting:
        soonest = waiting[0][2] - now
        soonest_min = int(soonest.total_seconds() // 60)
        sh, sm = divmod(soonest_min, 60)
        logger.debug(
            "check_pending: %d/%d ready — next in %dh %02dm",
            len(ready), len(all_deals), sh, sm,
        )
    else:
        logger.debug(
            "check_pending: %d/%d ready",
            len(ready), len(all_deals),
        )

    return [_deal_to_profile_dict(d) for d in ready if d.lead and d.lead.website]


def get_connected_profiles(session) -> list:
    """CONNECTED deals ready for follow-up."""
    from crm.models import Deal

    stage = _get_stage(ProfileState.CONNECTED)
    deals = list(
        Deal.objects.filter(
            stage=stage,
            owner=session.django_user,
        ).select_related("lead")
    )
    logger.debug("get_connected_profiles: %d CONNECTED deals", len(deals))

    return [_deal_to_profile_dict(d) for d in deals if d.lead and d.lead.website]


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
    return f"https://www.linkedin.com/in/{quote(public_id, safe='')}/"


def save_chat_message(session: "AccountSession", public_identifier: str, content: str):
    """Persist an outgoing message as a ChatMessage attached to the Lead."""
    from chat.models import ChatMessage
    from django.contrib.contenttypes.models import ContentType

    clean_url = public_id_to_url(public_identifier)
    lead, _deal = _get_or_create_lead_and_deal(session, public_identifier, clean_url)

    ct = ContentType.objects.get_for_model(lead)
    ChatMessage.objects.create(
        content_type=ct,
        object_id=lead.pk,
        content=content,
        owner=session.django_user,
    )
    logger.debug("Saved chat message for %s", public_identifier)


def debug_profile_preview(enriched):
    pretty = json.dumps(enriched, indent=2, ensure_ascii=False, default=str)
    preview_lines = pretty.splitlines()[:3]
    logger.debug("=== ENRICHED PROFILE PREVIEW ===\n%s\n...", "\n".join(preview_lines))
