import logging

from django.db import transaction
from termcolor import colored

from openoutreach.crm.models import DealState

logger = logging.getLogger(__name__)

_STATE_LOG_STYLE = {
    DealState.QUALIFIED: ("QUALIFIED", "green", []),
    DealState.READY_TO_FIND_EMAIL: ("READY_TO_FIND_EMAIL", "yellow", ["bold"]),
    DealState.READY_TO_EMAIL: ("READY_TO_EMAIL", "blue", ["bold"]),
    DealState.EMAILED: ("EMAILED", "blue", []),
    DealState.COMPLETED: ("COMPLETED", "green", ["bold"]),
    DealState.FAILED: ("FAILED", "red", ["bold"]),
}


def _deals_at_state(session, state: DealState) -> list:
    """Return profile dicts for all Deals at the given state in this campaign."""
    from openoutreach.crm.models import Deal

    qs = Deal.objects.filter(
        state=state,
        campaign=session.campaign,
    ).select_related("lead")
    return [d.lead.to_profile_dict() for d in qs]


def _existing_deal_or_lead(profile_url: str, campaign):
    """Check for an existing Deal in campaign; if none, look up the Lead.

    Returns (lead, existing_deal) — exactly one will be non-None,
    or both None if no Lead exists at all.
    """
    from openoutreach.crm.models import Deal, Lead

    existing = Deal.objects.filter(lead__profile_url=profile_url, campaign=campaign).first()
    if existing:
        return None, existing
    lead = Lead.objects.filter(profile_url=profile_url).first()
    return lead, None


# ── State transitions ──


def set_profile_state(session, profile_url: str, new_state: str, reason: str = "", outcome: str = ""):
    """Move the Deal to the corresponding state.

    Campaign-scoped: only finds Deals in the current campaign.
    Raises ValueError if no Deal exists.
    """
    from openoutreach.crm.models import Deal

    deal = (
        Deal.objects.filter(lead__profile_url=profile_url, campaign=session.campaign)
        .select_related("lead")
        .first()
    )
    if not deal:
        raise ValueError(f"No Deal for {profile_url} — cannot set state {new_state}")

    ps = DealState(new_state)
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
        logger.info("%s %s%s", profile_url, colored(label, color, attrs=attrs), suffix)
    else:
        logger.debug("%s %s (unchanged)%s", profile_url, label, suffix)


# ── State queries ──


def get_qualified_profiles(session) -> list:
    """QUALIFIED deals awaiting the rank gate.

    The single find-email-pool chokepoint: ``ready_pool`` promotes above the GP
    confidence threshold from here to READY_TO_FIND_EMAIL (the paid-lookup pool).
    """
    from openoutreach.crm.models import Deal

    qs = Deal.objects.filter(
        state=DealState.QUALIFIED,
        campaign=session.campaign,
    ).select_related("lead")
    return [d.lead.to_profile_dict() for d in qs]


def get_ready_to_find_email_profiles(session) -> list:
    return _deals_at_state(session, DealState.READY_TO_FIND_EMAIL)


def get_emailable_deals(session):
    """The email pool — Deals queued for their single Layer-1 email, oldest first.

    Symmetric with the connect pools above: each reads exactly one FSM state. The
    state alone is the eligibility — the qualify router reaches READY_TO_EMAIL only
    on a finder hit (so ``Lead.email`` is set), and the send moves it to EMAILED
    (so it is never-emailed). Returns ``Deal`` rows (not profile dicts — the EMAIL
    task acts on the Deal directly). ``disqualified`` guards a post-qualification
    do-not-contact, matching the follow_up pool.
    """
    from openoutreach.crm.models import Deal

    return (
        Deal.objects.filter(
            campaign=session.campaign,
            state=DealState.READY_TO_EMAIL,
            lead__disqualified=False,
        )
        .select_related("lead", "mailbox")
        .order_by("creation_date")
    )


# ── Deal creation ──


@transaction.atomic
def create_disqualified_deal(session, profile_url: str, reason: str = ""):
    """Create a FAILED Deal with 'Disqualified' closing reason for an LLM-rejected lead.

    LLM qualification rejections are tracked as FAILED Deals (campaign-scoped),
    NOT as Lead.disqualified (which is for permanent account-level exclusion).
    """
    from openoutreach.crm.models import Outcome

    campaign = session.campaign
    lead, existing = _existing_deal_or_lead(profile_url, campaign)
    if existing:
        return existing
    if not lead:
        logger.warning("create_disqualified_deal: no Lead for %s", profile_url)
        return None

    deal = _create_deal(
        lead=lead,
        state=DealState.FAILED,
        session=session,
        outcome=Outcome.WRONG_FIT,
        reason=reason,
    )

    suffix = f" ({reason})" if reason else ""
    logger.info("%s %s%s", profile_url, colored("DISQUALIFIED", "red", attrs=["bold"]), suffix)
    return deal


def create_freemium_deal(session, profile_url: str):
    """Create a QUALIFIED Deal in the freemium campaign for a candidate lead."""
    campaign = session.campaign
    lead, existing = _existing_deal_or_lead(profile_url, campaign)
    if existing:
        return existing
    if not lead:
        raise ValueError(f"No Lead for {profile_url}")

    deal = _create_deal(
        lead=lead,
        state=DealState.QUALIFIED,
        session=session,
    )

    logger.info("%s %s", profile_url, colored("FREEMIUM DEAL", "cyan", attrs=["bold"]))
    return deal


def _create_deal(
    *, lead, state, session,
    outcome="", reason="",
):
    """Shared Deal creation with common defaults."""
    from openoutreach.crm.models import Deal

    return Deal.objects.create(
        lead=lead,
        campaign=session.campaign,
        state=state,
        outcome=outcome,
        reason=reason,
    )
