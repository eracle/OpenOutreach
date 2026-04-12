import logging

from django.db import transaction
from termcolor import colored

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


def increment_connect_attempts(session, public_id: str) -> int:
    """Increment connect_attempts on the Deal and return the new count."""
    from crm.models import Deal

    deal = Deal.objects.filter(
        lead__public_identifier=public_id, campaign=session.campaign,
    ).first()
    if not deal:
        return 1

    deal.connect_attempts += 1
    deal.save(update_fields=["connect_attempts"])
    return deal.connect_attempts


def _deal_to_profile_dict(deal) -> dict:
    """Convert a Deal (with select_related lead) to a profile dict for lanes."""
    base = deal.lead.to_profile_dict()
    base["meta"] = {
        "connect_attempts": deal.connect_attempts,
        "backoff_hours": deal.backoff_hours,
        "reason": deal.reason,
    }
    return base


def _deals_at_state(session, state: ProfileState) -> list:
    """Return profile dicts for all Deals at the given state in this campaign."""
    from crm.models import Deal

    qs = Deal.objects.filter(
        state=state,
        campaign=session.campaign,
    ).select_related("lead")
    return [_deal_to_profile_dict(d) for d in qs]


def _existing_deal_or_lead(public_id: str, campaign):
    """Check for an existing Deal in campaign; if none, look up the Lead.

    Returns (lead, existing_deal) — exactly one will be non-None,
    or both None if no Lead exists at all.
    """
    from crm.models import Deal, Lead

    existing = Deal.objects.filter(lead__public_identifier=public_id, campaign=campaign).first()
    if existing:
        return None, existing
    lead = Lead.objects.filter(public_identifier=public_id).first()
    return lead, None


# ── State transitions ──


def set_profile_state(session, public_identifier: str, new_state: str, reason: str = "", outcome: str = ""):
    """Move the Deal to the corresponding state.

    Campaign-scoped: only finds Deals in the current campaign.
    Raises ValueError if no Deal exists.
    """
    from crm.models import Deal

    deal = Deal.objects.filter(lead__public_identifier=public_identifier, campaign=session.campaign).first()
    if not deal:
        raise ValueError(f"No Deal for {public_identifier} — cannot set state {new_state}")

    ps = ProfileState(new_state)
    state_changed = (deal.state != ps)

    deal.state = ps

    if reason:
        deal.reason = reason
    if outcome:
        deal.outcome = outcome

    deal.save()

    label, color, attrs = _STATE_LOG_STYLE.get(ps, ("ERROR", "red", ["bold"]))
    suffix = f" ({reason})" if reason else ""
    if state_changed:
        logger.info("%s %s%s", public_identifier, colored(label, color, attrs=attrs), suffix)
    else:
        logger.debug("%s %s (unchanged)%s", public_identifier, label, suffix)


# ── State queries ──


def get_qualified_profiles(session) -> list:
    return _deals_at_state(session, ProfileState.QUALIFIED)


def get_ready_to_connect_profiles(session) -> list:
    return _deals_at_state(session, ProfileState.READY_TO_CONNECT)


def get_profile_dict_for_public_id(session, public_id: str) -> dict | None:
    """Load profile dict for a single public_id from Deal + Lead (campaign-scoped)."""
    from crm.models import Deal

    deal = (
        Deal.objects.filter(lead__public_identifier=public_id, campaign=session.campaign)
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
    NOT as Lead.disqualified (which is for permanent account-level exclusion).
    """
    from crm.models import Outcome

    campaign = session.campaign
    lead, existing = _existing_deal_or_lead(public_id, campaign)
    if existing:
        return existing
    if not lead:
        logger.warning("create_disqualified_deal: no Lead for %s", public_id)
        return None

    deal = _create_deal(
        lead=lead,
        state=ProfileState.FAILED,
        session=session,
        outcome=Outcome.WRONG_FIT,
        reason=reason,
    )

    suffix = f" ({reason})" if reason else ""
    logger.info("%s %s%s", public_id, colored("DISQUALIFIED", "red", attrs=["bold"]), suffix)
    return deal


@transaction.atomic
def create_freemium_deal(session, public_id: str):
    """Create a Deal in the freemium campaign for a candidate lead."""
    campaign = session.campaign
    lead, existing = _existing_deal_or_lead(public_id, campaign)
    if existing:
        return existing
    if not lead:
        raise ValueError(f"No Lead for {public_id}")

    deal = _create_deal(
        lead=lead,
        state=ProfileState.QUALIFIED,
        session=session,
    )

    logger.info("%s %s", public_id, colored("FREEMIUM DEAL", "cyan", attrs=["bold"]))
    return deal


def _create_deal(
    *, lead, state, session,
    outcome="", reason="",
):
    """Shared Deal creation with common defaults."""
    from crm.models import Deal

    return Deal.objects.create(
        lead=lead,
        campaign=session.campaign,
        state=state,
        outcome=outcome,
        reason=reason,
    )
