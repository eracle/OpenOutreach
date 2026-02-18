# linkedin/db/crm_profiles.py
"""
Profile CRUD backed by DjangoCRM models (Lead, Contact, Company, Deal).

Data model:
- Lead = prospect pool (enriched profiles awaiting qualification + disqualified)
- Contact = qualified profiles only (promoted from Lead)
- Deal = per-Contact pipeline entry, created only at qualification time

Pre-Deal states are implicit (derived from Lead attributes):
  URL-only:     Lead.description is empty/null
  Enriched:     Lead.description populated AND not disqualified AND no contact
  Disqualified: Lead.disqualified = True
  Qualified:    Lead.contact is not null

Deal stages (post-qualification): New, Pending, Connected, Completed, Failed
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

from linkedin.conf import PARTNER_LOG_LEVEL
from linkedin.navigation.enums import ProfileState

logger = logging.getLogger(__name__)

# Maps ProfileState enum values to Stage names in the CRM.
STATE_TO_STAGE = {
    ProfileState.NEW: "New",
    ProfileState.PENDING: "Pending",
    ProfileState.CONNECTED: "Connected",
    ProfileState.COMPLETED: "Completed",
    ProfileState.FAILED: "Failed",
}

# Reverse lookup: stage name -> ProfileState value
_STAGE_TO_STATE = {v: k.value for k, v in STATE_TO_STAGE.items()}


def _make_ticket() -> str:
    """Generate a unique 16-char ticket for a Deal."""
    return uuid.uuid4().hex[:16]


def _get_stage(state: ProfileState, session):
    from crm.models import Stage
    dept = session.campaign.department
    stage_name = STATE_TO_STAGE[state]
    return Stage.objects.get(name=stage_name, department=dept)


def _get_lead_source(session):
    from crm.models import LeadSource
    dept = session.campaign.department
    return LeadSource.objects.get(name="LinkedIn Scraper", department=dept)


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


def _lead_profile(lead) -> Optional[dict]:
    """Return the parsed profile dict stored as description on the Lead."""
    if not lead.description:
        return None
    try:
        return json.loads(lead.description)
    except (json.JSONDecodeError, TypeError):
        return None


# ── Lead-level operations (pre-Deal) ──


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
    """Create Lead with full profile data + Company. Returns lead PK or None if exists.

    Does NOT create Contact or Deal — those come at qualification.
    """
    from crm.models import Lead

    public_id = url_to_public_id(url)
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

    logger.debug("Created enriched lead for %s (pk=%d)", public_id, lead.pk)
    return lead.pk


def disqualify_lead(session, public_id: str, reason: str = ""):
    """Set Lead.disqualified = True and delete any existing Deal."""
    from crm.models import Lead, Deal

    clean_url = public_id_to_url(public_id)
    lead = Lead.objects.filter(website=clean_url).first()
    if not lead:
        logger.warning("disqualify_lead: no Lead for %s", public_id)
        return

    lead.disqualified = True
    if reason:
        lead.description = lead.description or ""
    lead.save()

    Deal.objects.filter(lead=lead, owner=session.django_user).delete()

    color_label = colored("DISQUALIFIED", "red", attrs=["bold"])
    suffix = f" ({reason})" if reason else ""
    logger.info("%s %s%s", public_id, color_label, suffix)


@transaction.atomic
def promote_lead_to_contact(session, public_id: str):
    """Create Contact from Lead + Deal at 'New' stage.

    Returns (contact, deal). Raises ValueError if Lead has no Company.
    """
    from crm.models import Lead, Contact, Deal

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

    # Create Deal at "New" stage
    deal = Deal.objects.create(
        name=f"LinkedIn: {public_id}",
        lead=lead,
        contact=contact,
        company=company,
        stage=_get_stage(ProfileState.NEW, session),
        owner=session.django_user,
        department=dept,
        next_step="",
        next_step_date=date.today(),
        ticket=_make_ticket(),
    )

    logger.info("%s %s", public_id, colored("QUALIFIED", "green", attrs=["bold"]))
    return contact, deal


def get_leads_for_qualification(session) -> list:
    """Leads with description, not disqualified, no contact FK."""
    from crm.models import Lead

    leads = Lead.objects.filter(
        owner=session.django_user,
        disqualified=False,
        contact__isnull=True,
    ).exclude(
        description__isnull=True,
    ).exclude(
        description="",
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


def count_leads_for_qualification(session) -> int:
    """Count of leads eligible for qualification."""
    from crm.models import Lead

    return Lead.objects.filter(
        owner=session.django_user,
        disqualified=False,
        contact__isnull=True,
    ).exclude(
        description__isnull=True,
    ).exclude(
        description="",
    ).count()


def pipeline_needs_refill(session, min_leads: int) -> bool:
    """Return True when the qualification pipeline is running low and needs more leads.

    Override this function to change the refill strategy.
    """
    return count_leads_for_qualification(session) < min_leads


# ── Deal-level operations (post-qualification) ──


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


def set_profile_state(
    session: "AccountSession",
    public_identifier: str,
    new_state: str,
    reason: str = "",
):
    """
    Move the Deal linked to this Lead to the corresponding Stage.
    Only handles Deal states (NEW, PENDING, CONNECTED, COMPLETED, FAILED).
    Raises ValueError if no Deal exists.
    """
    from crm.models import Deal, ClosingReason

    clean_url = public_id_to_url(public_identifier)
    deal = Deal.objects.filter(lead__website=clean_url, owner=session.django_user).first()
    if not deal:
        raise ValueError(f"No Deal for {public_identifier} — cannot set state {new_state}")

    ps = ProfileState(new_state)
    old_stage_name = deal.stage.name if deal.stage else None
    new_stage = _get_stage(ps, session)
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

    dept = session.campaign.department

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

    deal.save()

    _STATE_LOG_STYLE = {
        ProfileState.NEW: ("NEW", "green", []),
        ProfileState.PENDING: ("PENDING", "cyan", []),
        ProfileState.CONNECTED: ("CONNECTED", "green", ["bold"]),
        ProfileState.COMPLETED: ("COMPLETED", "green", ["bold"]),
        ProfileState.FAILED: ("FAILED", "red", ["bold"]),
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

    # Derive state from Deal stage if present, otherwise from Lead attributes
    if deal and deal.stage:
        state = _STAGE_TO_STATE.get(deal.stage.name, ProfileState.NEW.value)
    elif getattr(lead, 'disqualified', False):
        state = "disqualified"
    elif lead.description:
        state = "enriched"
    else:
        state = "url_only"

    return {
        "state": state,
        "profile": _lead_profile(lead),
    }


def get_qualified_profiles(session) -> list:
    """All Deals at 'New' stage for this user (qualified, ready for connect)."""
    from crm.models import Deal

    stage = _get_stage(ProfileState.NEW, session)
    qs = Deal.objects.filter(
        stage=stage,
        owner=session.django_user,
    ).select_related("lead")

    # Partner campaigns re-use disqualified leads, so skip the filter.
    if not getattr(session.campaign, "is_partner", False):
        qs = qs.filter(lead__disqualified=False)

    return [_deal_to_profile_dict(d) for d in qs if d.lead and d.lead.website]


def count_qualified_profiles(session) -> int:
    """Count Deals at 'New' stage."""
    from crm.models import Deal

    stage = _get_stage(ProfileState.NEW, session)
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
    stage = _get_stage(ProfileState.PENDING, session)
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

    stage = _get_stage(ProfileState.CONNECTED, session)
    deals = list(
        Deal.objects.filter(
            stage=stage,
            owner=session.django_user,
        ).select_related("lead")
    )
    logger.debug("get_connected_profiles: %d CONNECTED deals", len(deals))

    return [_deal_to_profile_dict(d) for d in deals if d.lead and d.lead.website]


def get_updated_at_map(session: "AccountSession", public_identifiers: List[str]) -> dict:
    """
    Return a dict mapping public_identifier -> update_date for existing Leads.
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


# ── Partner campaign helpers ──


def seed_partner_deals(session) -> int:
    """Create deals in current campaign's department from disqualified leads with embeddings.

    Returns the number of new deals created.
    """
    from crm.models import Lead, Deal
    from linkedin.ml.embeddings import get_embedded_lead_ids

    dept = session.campaign.department

    disqualified_pks = set(
        Lead.objects.filter(disqualified=True).values_list("pk", flat=True)
    )
    embedded_ids = get_embedded_lead_ids()
    eligible_pks = sorted(disqualified_pks & embedded_ids)

    if not eligible_pks:
        return 0

    from crm.models import Stage
    stage = Stage.objects.filter(name="New", department=dept).first()
    if stage is None:
        return 0

    created = 0
    for lead_pk in eligible_pks:
        lead = Lead.objects.filter(pk=lead_pk).first()
        if not lead:
            continue

        # Skip if deal already exists in this department
        if Deal.objects.filter(lead=lead, department=dept).exists():
            continue

        Deal.objects.create(
            name=f"Partner: {url_to_public_id(lead.website) or lead.pk}",
            lead=lead,
            contact=lead.contact,
            company=lead.company,
            stage=stage,
            owner=session.django_user,
            department=dept,
            next_step="",
            next_step_date=date.today(),
            ticket=_make_ticket(),
        )
        created += 1

    if created:
        logger.log(PARTNER_LOG_LEVEL, "[Partner] Seeded %d partner deals in %s", created, dept.name)

    return created


# ── Internal helpers ──


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


# ── Pure URL helpers (no DB dependency) ──
def url_to_public_id(url: str) -> Optional[str]:
    """
    Strict LinkedIn public ID extractor:
    - Path MUST start with /in/
    - Returns the second segment, percent-decoded
    - Returns None for empty or non-profile URLs
    """
    if not url:
        return None

    path = urlparse(url.strip()).path
    parts = path.strip("/").split("/")

    if len(parts) < 2 or parts[0] != "in":
        return None

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
    from crm.models import Lead

    clean_url = public_id_to_url(public_identifier)
    lead = Lead.objects.filter(website=clean_url).first()
    if not lead:
        logger.warning("save_chat_message: no Lead for %s", public_identifier)
        return

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
