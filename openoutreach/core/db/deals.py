import logging

from django.db import transaction
from termcolor import colored

from openoutreach.crm.models import DealState

logger = logging.getLogger(__name__)

_STATE_LOG_STYLE = {
    DealState.QUALIFIED: ("QUALIFIED", "green", []),
    DealState.READY_TO_CONNECT: ("READY_TO_CONNECT", "yellow", ["bold"]),
    DealState.READY_TO_EMAIL: ("READY_TO_EMAIL", "blue", ["bold"]),
    DealState.EMAILED: ("EMAILED", "blue", []),
    DealState.PENDING: ("PENDING", "cyan", []),
    DealState.CONNECTED: ("CONNECTED", "green", ["bold"]),
    DealState.COMPLETED: ("COMPLETED", "green", ["bold"]),
    DealState.FAILED: ("FAILED", "red", ["bold"]),
}


def increment_connect_attempts(session, public_id: str) -> int:
    """Increment connect_attempts on the Deal and return the new count."""
    from openoutreach.crm.models import Deal

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


def _deals_at_state(session, state: DealState) -> list:
    """Return profile dicts for all Deals at the given state in this campaign."""
    from openoutreach.crm.models import Deal

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
    from openoutreach.crm.models import Deal, Lead

    existing = Deal.objects.filter(lead__public_identifier=public_id, campaign=campaign).first()
    if existing:
        return None, existing
    lead = Lead.objects.filter(public_identifier=public_id).first()
    return lead, None


# ── State transitions ──


def _capture_contact_info(lead, session) -> None:
    """Best-effort LinkedIn contact-info capture when a lead first connects.

    Fired on the CONNECTED transition — the moment LinkedIn exposes a 1st-degree
    connection's email(s). On success, those addresses are the second give-back
    moment to the central store. A failure here must never roll back the
    transition or fail the task, so expected scrape/network errors are swallowed
    with a log; ``AuthenticationError`` still propagates (the daemon's reauth
    handler owns it, and capture is moot on a dead session).
    """
    from linkedin_cli.exceptions import ProfileInaccessibleError
    from openoutreach.contacts import service as contacts

    try:
        lead.capture_contact_info(session)
    except (ProfileInaccessibleError, IOError) as exc:
        logger.warning("contact-info capture failed for %s: %s", lead.public_identifier, exc)
        return

    emails = (lead.contact_info or {}).get("emails") or []
    contacts.contribute(session, lead, emails)


def set_profile_state(session, public_identifier: str, new_state: str, reason: str = "", outcome: str = ""):
    """Move the Deal to the corresponding state and enqueue the implied next task.

    Campaign-scoped: only finds Deals in the current campaign.
    Raises ValueError if no Deal exists.

    Task creation for state-driven transitions (CONNECTED → follow_up,
    PENDING → check_pending) happens here via the scheduler hook — callers
    do not enqueue directly.
    """
    from openoutreach.crm.models import Deal
    from openoutreach.core.scheduler import on_deal_state_entered

    deal = (
        Deal.objects.filter(lead__public_identifier=public_identifier, campaign=session.campaign)
        .select_related("lead")
        .first()
    )
    if not deal:
        raise ValueError(f"No Deal for {public_identifier} — cannot set state {new_state}")

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
        logger.info("%s %s%s", public_identifier, colored(label, color, attrs=attrs), suffix)
    else:
        logger.debug("%s %s (unchanged)%s", public_identifier, label, suffix)

    on_deal_state_entered(deal)

    if state_changed and ps == DealState.CONNECTED:
        _capture_contact_info(deal.lead, session)


# ── State queries ──


def get_qualified_profiles(session) -> list:
    """Connect-eligible QUALIFIED deals — those WITHOUT a resolved email.

    Enrichment routes, it doesn't gate: a lead with ``api_email`` is reached on
    the EMAIL channel and excluded here, so the scarce connect is spent only on
    leads whose only door is LinkedIn (the connection then harvests their
    contact info on acceptance). This is the single connect-pool chokepoint —
    ready_pool promotes from here, so email-having leads never reach
    READY_TO_CONNECT.
    """
    from openoutreach.crm.models import Deal

    qs = Deal.objects.filter(
        state=DealState.QUALIFIED,
        campaign=session.campaign,
        lead__api_email__isnull=True,
    ).select_related("lead")
    return [_deal_to_profile_dict(d) for d in qs]


def get_ready_to_connect_profiles(session) -> list:
    return _deals_at_state(session, DealState.READY_TO_CONNECT)


def get_emailable_deals(session):
    """The email pool — Deals queued for their single Layer-1 email, oldest first.

    Symmetric with the connect pools above: each reads exactly one FSM state. The
    state alone is the eligibility — the qualify router reaches READY_TO_EMAIL only
    on a finder hit (so ``Lead.api_email`` is set), and the send moves it to EMAILED
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


def get_profile_dict_for_public_id(session, public_id: str) -> dict | None:
    """Load profile dict for a single public_id from Deal + Lead (campaign-scoped)."""
    from openoutreach.crm.models import Deal

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
    from openoutreach.crm.models import Outcome

    campaign = session.campaign
    lead, existing = _existing_deal_or_lead(public_id, campaign)
    if existing:
        return existing
    if not lead:
        logger.warning("create_disqualified_deal: no Lead for %s", public_id)
        return None

    deal = _create_deal(
        lead=lead,
        state=DealState.FAILED,
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
        state=DealState.QUALIFIED,
        session=session,
    )

    logger.info("%s %s", public_id, colored("FREEMIUM DEAL", "cyan", attrs=["bold"]))
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
