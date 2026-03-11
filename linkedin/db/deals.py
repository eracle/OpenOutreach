import json
import logging
from datetime import date, timedelta
from typing import Optional

from django.db import transaction
from django.utils import timezone
from termcolor import colored

from linkedin.db._helpers import _make_ticket, _get_stage
from linkedin.db.leads import _lead_profile
from linkedin.db.urls import url_to_public_id, public_id_to_url
from linkedin.enums import ProfileState

logger = logging.getLogger(__name__)


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


def set_profile_state(
    session: "AccountSession",
    public_identifier: str,
    new_state: str,
    reason: str = "",
):
    """
    Move the Deal linked to this Lead to the corresponding Stage.
    Only handles Deal states (QUALIFIED, READY_TO_CONNECT, PENDING, CONNECTED, COMPLETED, FAILED).
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

    old_is_pending = (old_stage_name == ProfileState.PENDING.value)
    new_is_pending = (ps == ProfileState.PENDING)

    deal.stage = new_stage
    deal.change_stage_data(date.today())
    deal.next_step_date = date.today()

    # Clear backoff metadata on transitions into or out of PENDING (not same-state)
    if old_is_pending != new_is_pending:
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
        ProfileState.QUALIFIED: ("QUALIFIED", "green", []),
        ProfileState.READY_TO_CONNECT: ("READY_TO_CONNECT", "yellow", ["bold"]),
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


def get_qualified_profiles(session) -> list:
    """All Deals at 'Qualified' stage for this user."""
    from crm.models import Deal

    stage = _get_stage(ProfileState.QUALIFIED, session)
    qs = Deal.objects.filter(
        stage=stage,
        owner=session.django_user,
        lead__disqualified=False,
    ).select_related("lead")

    return [_deal_to_profile_dict(d) for d in qs]


def count_qualified_profiles(session) -> int:
    """Count Deals at 'Qualified' stage."""
    from crm.models import Deal

    stage = _get_stage(ProfileState.QUALIFIED, session)
    qs = Deal.objects.filter(
        stage=stage,
        owner=session.django_user,
        lead__disqualified=False,
    )
    return qs.count()


def get_ready_to_connect_profiles(session) -> list:
    """All Deals at 'Ready to Connect' stage for this user."""
    from crm.models import Deal

    stage = _get_stage(ProfileState.READY_TO_CONNECT, session)
    qs = Deal.objects.filter(
        stage=stage,
        owner=session.django_user,
        lead__disqualified=False,
    ).select_related("lead")

    return [_deal_to_profile_dict(d) for d in qs]


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
        meta = parse_next_step(d)
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

    return [_deal_to_profile_dict(d) for d in ready]


def get_profile_dict_for_public_id(session, public_id: str) -> dict | None:
    """Load profile dict for a single public_id from Deal + Lead."""
    from crm.models import Deal

    clean_url = public_id_to_url(public_id)
    deal = (
        Deal.objects.filter(lead__website=clean_url, owner=session.django_user)
        .select_related("lead")
        .first()
    )
    if not deal:
        return None
    return _deal_to_profile_dict(deal)


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

    return [_deal_to_profile_dict(d) for d in deals]


@transaction.atomic
def create_partner_deal(session, public_id: str):
    """Create a single Deal in the partner campaign's department for a disqualified lead.

    Called just-in-time when a partner candidate is selected for connection,
    instead of bulk-creating Deals for all disqualified leads upfront.

    Returns the created Deal, or the existing Deal if one already exists.
    """
    from crm.models import Deal

    clean_url = public_id_to_url(public_id)
    dept = session.campaign.department

    existing = Deal.objects.filter(lead__website=clean_url, department=dept).first()
    if existing:
        return existing

    from crm.models import Lead

    lead = Lead.objects.filter(website=clean_url).first()
    if not lead:
        raise ValueError(f"No Lead for {public_id}")

    deal = Deal.objects.create(
        name=f"Partner: {public_id}",
        lead=lead,
        contact=lead.contact,
        company=lead.company,
        stage=_get_stage(ProfileState.QUALIFIED, session),
        owner=session.django_user,
        department=dept,
        next_step="",
        next_step_date=date.today(),
        ticket=_make_ticket(),
    )
    logger.info("%s %s", public_id, colored("PARTNER DEAL", "cyan", attrs=["bold"]))
    return deal
