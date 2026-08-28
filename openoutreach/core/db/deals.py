import logging

from django.db import transaction
from termcolor import colored

from openoutreach.crm.models import DealState

logger = logging.getLogger(__name__)

# Keep in sync with DealState: every state a Deal can transition *into* needs an
# entry here, or set_profile_state falls back to a red "ERROR" label (see below).
# NO_EMAIL_BETTERCONTACT is an enrichment miss (provider found no address) — an
# expected terminal, not an operational error, so it renders muted yellow.
_STATE_LOG_STYLE = {
    DealState.QUALIFIED: ("QUALIFIED", "green", []),
    DealState.READY_TO_FIND_EMAIL: ("READY_TO_FIND_EMAIL", "yellow", ["bold"]),
    DealState.FINDING_EMAIL: ("FINDING_EMAIL", "cyan", []),
    DealState.RESOLVED: ("RESOLVED", "green", ["bold"]),
    DealState.NO_EMAIL_BETTERCONTACT: ("NO EMAIL", "yellow", []),
    DealState.FAILED: ("FAILED", "red", ["bold"]),
}


def _deals_at_state(campaign, state: DealState) -> list:
    """Return profile dicts for all Deals at the given state in this campaign."""
    from openoutreach.crm.models import Deal

    qs = Deal.objects.filter(
        state=state,
        campaign=campaign,
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


def set_profile_state(campaign, profile_url: str, new_state: str, reason: str = "", outcome: str = "", log: bool = True):
    """Move the Deal to the corresponding state.

    Campaign-scoped: only finds Deals in the current campaign.
    Raises ValueError if no Deal exists.

    ``log`` emits the standalone ``<url> STATE`` spine line. Callers that render
    their own aligned block (the email pipeline's ``find_email`` / ``collect_email``
    handlers) pass ``log=False`` and fold the resulting state into a block step,
    so the transition isn't logged twice.
    """
    from openoutreach.crm.models import Deal

    deal = (
        Deal.objects.filter(lead__profile_url=profile_url, campaign=campaign)
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
    if not log:
        return
    if state_changed:
        logger.info("%s %s%s", profile_url, colored(label, color, attrs=attrs), suffix)
    else:
        logger.debug("%s %s (unchanged)%s", profile_url, label, suffix)


# ── State queries ──


def get_qualified_profiles(campaign) -> list:
    """QUALIFIED deals awaiting the rank gate.

    The single find-email-pool chokepoint: ``ready_pool`` promotes above the GP
    confidence threshold from here to READY_TO_FIND_EMAIL (the paid-lookup pool).
    """
    from openoutreach.crm.models import Deal

    qs = Deal.objects.filter(
        state=DealState.QUALIFIED,
        campaign=campaign,
    ).select_related("lead")
    return [d.lead.to_profile_dict() for d in qs]


def get_ready_to_find_email_profiles(campaign) -> list:
    return _deals_at_state(campaign, DealState.READY_TO_FIND_EMAIL)


# ── Deal creation ──


@transaction.atomic
def create_disqualified_deal(campaign, profile_url: str, reason: str = ""):
    """Create a FAILED Deal with 'Disqualified' closing reason for an LLM-rejected lead.

    LLM qualification rejections are tracked as FAILED Deals (campaign-scoped),
    NOT as Lead.disqualified (which is for permanent account-level exclusion).

    **Says nothing** — the rejection is announced by the step that reached it
    (``pipeline.qualify``). Announcing here too printed the same reason twice in a row,
    once behind a URL and DISQUALIFIED and once behind ✗ and the person's name.
    """
    from openoutreach.crm.models import Outcome

    lead, existing = _existing_deal_or_lead(profile_url, campaign)
    if existing:
        return existing
    if not lead:
        logger.warning("create_disqualified_deal: no Lead for %s", profile_url)
        return None

    deal = _create_deal(
        lead=lead,
        state=DealState.FAILED,
        campaign=campaign,
        outcome=Outcome.WRONG_FIT,
        reason=reason,
    )

    return deal


# ``create_freemium_deal`` lived here — it claimed a lead another campaign had already
# discovered into the promo campaign, so the maintainer's own advertisement could be
# sent to them from the operator's mailbox. Gone with that campaign.


def _create_deal(
    *, lead, state, campaign,
    outcome="", reason="",
):
    """Shared Deal creation with common defaults."""
    from openoutreach.crm.models import Deal

    return Deal.objects.create(
        lead=lead,
        campaign=campaign,
        state=state,
        outcome=outcome,
        reason=reason,
    )
