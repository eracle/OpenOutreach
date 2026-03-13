import json
import logging
from datetime import date

from django.db import transaction
from django.utils import timezone
from termcolor import colored

from linkedin.db._helpers import _make_ticket, _get_stage
from linkedin.db.leads import _lead_profile
from linkedin.db.urls import url_to_public_id, public_id_to_url
from linkedin.enums import ProfileState

logger = logging.getLogger(__name__)

_STATE_LOG_STYLE = {
    ProfileState.QUALIFIED: ("QUALIFIED", "green", []),
    ProfileState.READY_TO_CONNECT: ("READY_TO_CONNECT", "yellow", ["bold"]),
    ProfileState.PENDING: ("PENDING", "cyan", []),
    ProfileState.CONNECTED: ("CONNECTED", "green", ["bold"]),
    ProfileState.COMPLETED: ("COMPLETED", "green", ["bold"]),
    ProfileState.FAILED: ("FAILED", "red", ["bold"]),
}


def parse_next_step(deal) -> dict:
    """Parse deal.next_step as JSON, return empty dict on failure or empty string."""
    if not deal.next_step:
        return {}
    try:
        return json.loads(deal.next_step)
    except (json.JSONDecodeError, TypeError):
        return {}


def _deal_to_profile_dict(deal) -> dict:
    """Convert a Deal (with select_related lead) to a profile dict for lanes."""
    lead = deal.lead
    profile = _lead_profile(lead) or {}
    public_id = url_to_public_id(lead.website) if lead.website else ""
    return {
        "lead_id": lead.pk,
        "public_identifier": public_id,
        "url": lead.website or "",
        "profile": profile,
        "meta": parse_next_step(deal),
    }


def _deals_at_stage(session, state: ProfileState) -> list:
    """Return profile dicts for all Deals at the given stage in this campaign's department."""
    from crm.models import Deal

    stage = _get_stage(state, session)
    qs = Deal.objects.filter(
        stage=stage,
        owner=session.django_user,
        department=session.campaign.department,
    ).select_related("lead")
    return [_deal_to_profile_dict(d) for d in qs]


def _existing_deal_or_lead(public_id: str, dept):
    """Check for an existing Deal in dept; if none, look up the Lead.

    Returns (lead, existing_deal) — exactly one will be non-None,
    or both None if no Lead exists at all.
    """
    from crm.models import Deal, Lead

    clean_url = public_id_to_url(public_id)
    existing = Deal.objects.filter(lead__website=clean_url, department=dept).first()
    if existing:
        return None, existing
    lead = Lead.objects.filter(website=clean_url).first()
    return lead, None


# ── State transitions ──


def set_profile_state(session, public_identifier: str, new_state: str, reason: str = ""):
    """Move the Deal linked to this Lead to the corresponding Stage.

    Department-scoped: only finds Deals in the current campaign's department.
    Raises ValueError if no Deal exists.
    """
    from crm.models import Deal, ClosingReason

    clean_url = public_id_to_url(public_identifier)
    dept = session.campaign.department
    deal = Deal.objects.filter(lead__website=clean_url, owner=session.django_user, department=dept).first()
    if not deal:
        raise ValueError(f"No Deal for {public_identifier} — cannot set state {new_state}")

    ps = ProfileState(new_state)
    old_stage_name = deal.stage.name if deal.stage else None
    new_stage = _get_stage(ps, session)
    state_changed = (old_stage_name != new_stage.name)

    deal.stage = new_stage
    deal.change_stage_data(date.today())
    deal.next_step_date = date.today()

    # Clear backoff metadata on transitions into or out of PENDING (not same-state)
    old_is_pending = (old_stage_name == ProfileState.PENDING.value)
    if old_is_pending != (ps == ProfileState.PENDING):
        deal.next_step = ""

    if reason:
        deal.description = reason

    if ps == ProfileState.FAILED:
        closing = ClosingReason.objects.filter(name="Failed", department=dept).first()
        if closing:
            deal.closing_reason = closing
        deal.active = False

    if ps == ProfileState.COMPLETED:
        closing = ClosingReason.objects.filter(name="Completed", department=dept).first()
        if closing:
            deal.closing_reason = closing
        deal.win_closing_date = timezone.now()

    deal.save()

    label, color, attrs = _STATE_LOG_STYLE.get(ps, ("ERROR", "red", ["bold"]))
    suffix = f" ({reason})" if reason else ""
    if state_changed:
        logger.info("%s %s%s", public_identifier, colored(label, color, attrs=attrs), suffix)
    else:
        logger.debug("%s %s (unchanged)%s", public_identifier, label, suffix)


# ── Stage queries ──
# No lead__disqualified filter needed: Deal existence at a stage implies
# the lead passed qualification for this campaign. disqualified=True (self-profile)
# never gets a Deal.


def get_qualified_profiles(session) -> list:
    return _deals_at_stage(session, ProfileState.QUALIFIED)


def get_ready_to_connect_profiles(session) -> list:
    return _deals_at_stage(session, ProfileState.READY_TO_CONNECT)


def get_profile_dict_for_public_id(session, public_id: str) -> dict | None:
    """Load profile dict for a single public_id from Deal + Lead (department-scoped)."""
    from crm.models import Deal

    clean_url = public_id_to_url(public_id)
    dept = session.campaign.department
    deal = (
        Deal.objects.filter(lead__website=clean_url, owner=session.django_user, department=dept)
        .select_related("lead")
        .first()
    )
    if not deal:
        return None
    return _deal_to_profile_dict(deal)


# ── Deal creation ──


@transaction.atomic
def create_disqualified_deal(session, public_id: str, reason: str = ""):
    """Create a FAILED Deal with 'Disqualified' closing reason for an LLM-rejected lead.

    LLM qualification rejections are tracked as FAILED Deals (campaign-scoped),
    NOT as Lead.disqualified (which is reserved for self-profile exclusion only).
    A lead can be rejected in one campaign but still be eligible for other campaigns.
    """
    from crm.models import ClosingReason

    dept = session.campaign.department
    lead, existing = _existing_deal_or_lead(public_id, dept)
    if existing:
        return existing
    if not lead:
        logger.warning("create_disqualified_deal: no Lead for %s", public_id)
        return None

    closing = ClosingReason.objects.filter(name="Disqualified", department=dept).first()
    deal = _create_deal(
        name=f"LinkedIn: {public_id}",
        lead=lead,
        stage=_get_stage(ProfileState.FAILED, session),
        session=session,
        closing_reason=closing,
        description=reason,
        active=False,
    )

    suffix = f" ({reason})" if reason else ""
    logger.info("%s %s%s", public_id, colored("DISQUALIFIED", "red", attrs=["bold"]), suffix)
    return deal


@transaction.atomic
def create_freemium_deal(session, public_id: str):
    """Create a Deal in the freemium campaign's department for a candidate lead.

    Called just-in-time when a freemium candidate is selected for connection.
    Returns the created Deal, or the existing Deal if one already exists.
    """
    dept = session.campaign.department
    lead, existing = _existing_deal_or_lead(public_id, dept)
    if existing:
        return existing
    if not lead:
        raise ValueError(f"No Lead for {public_id}")

    deal = _create_deal(
        name=f"Freemium: {public_id}",
        lead=lead,
        stage=_get_stage(ProfileState.QUALIFIED, session),
        session=session,
        contact=lead.contact,
        company=lead.company,
    )

    logger.info("%s %s", public_id, colored("FREEMIUM DEAL", "cyan", attrs=["bold"]))
    return deal


def _create_deal(
    *, name, lead, stage, session,
    contact=None, company=None, closing_reason=None,
    description="", active=True, next_step="", next_step_date=None,
):
    """Shared Deal creation with common defaults."""
    from crm.models import Deal

    return Deal.objects.create(
        name=name,
        lead=lead,
        stage=stage,
        owner=session.django_user,
        department=session.campaign.department,
        contact=contact,
        company=company,
        closing_reason=closing_reason,
        description=description,
        active=active,
        next_step=next_step,
        next_step_date=next_step_date or date.today(),
        ticket=_make_ticket(),
    )
