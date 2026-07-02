# openoutreach/emails/tasks/find_email.py
"""FIND_EMAIL task — the paid-lookup leg that replaces the LinkedIn connect leg.

Drives the discovery→qualify→rank chain to surface one top-ranked
READY_TO_FIND_EMAIL deal, then looks up its work email — free hub cache first,
paid BetterContact second. The result is tri-state, and the third case is *not*
a lead disposition:

    hit          → READY_TO_EMAIL   (an address exists; 1 credit spent on a paid hit)
    miss         → FAILED, reason="no email", outcome blank (terminal — checked once)
    couldn't-run → stays READY_TO_FIND_EMAIL (no key / out of credits / API down —
                   the lookup never happened, so retry next cycle)

A miss leaves ``outcome`` blank on purpose: the ML labeler reads FAILED+wrong_fit
as a negative and skips every other FAILED deal, so a lead we simply couldn't find
an address for is ML-skipped, never scored as a bad fit. An out-of-credits
response must map to couldn't-run, never to miss — else we'd FAIL a lead we never
actually checked.
"""
from __future__ import annotations

import logging

from termcolor import colored

from openoutreach.crm.models import DealState

logger = logging.getLogger(__name__)


def _select_candidate(session, campaign, qualifier):
    """Pick the next lead to look up an email for, ensuring it has a Deal.

    Freemium campaigns draw from the kit-ranked freemium pool and mint the Deal on
    the fly (the kit model ranks in place of the GP gate); regular campaigns draw
    from the rank-gated READY_TO_FIND_EMAIL pool, where the Deal already exists.
    """
    if campaign.is_freemium:
        from openoutreach.core.db.deals import create_freemium_deal
        from openoutreach.linkedin.pipeline.freemium_pool import find_freemium_candidate

        candidate = find_freemium_candidate(session, qualifier)
        if candidate is not None:
            create_freemium_deal(session, candidate["public_identifier"])
        return candidate

    from openoutreach.linkedin.pipeline.pools import find_candidate

    return find_candidate(session, qualifier)


def handle_find_email(task, session, qualifiers):
    from openoutreach.core.db.deals import set_profile_state
    from openoutreach.crm.models import Deal
    from openoutreach.emails.models import has_mailbox

    campaign = session.campaign

    # No mailbox → nothing to send even on a hit, so resolving an address (and
    # spending a credit) is pointless. Onboarding collects the mailbox; until
    # one is connected the leg is idle.
    if not has_mailbox():
        logger.info("[%s] find_email: no mailbox — leg idle until one is connected", campaign)
        return

    qualifier = qualifiers.get(campaign.pk)
    candidate = _select_candidate(session, campaign, qualifier)
    if candidate is None:
        logger.info("[%s] find_email: no ranked candidate awaiting a lookup", campaign)
        return

    public_id = candidate["public_identifier"]
    deal = (
        Deal.objects.filter(lead__public_identifier=public_id, campaign=campaign)
        .select_related("lead")
        .first()
    )
    if deal is None:
        logger.warning("[%s] find_email: no Deal for %s", campaign, public_id)
        return

    logger.info("[%s] %s %s", campaign, colored("▶ find_email", "cyan", attrs=["bold"]), public_id)

    result = _resolve_email(session, deal.lead)
    if result == "hit":
        set_profile_state(session, public_id, DealState.READY_TO_EMAIL.value)
    elif result == "miss":
        set_profile_state(session, public_id, DealState.FAILED.value, reason="no email")
    else:  # "unavailable" — the lookup never ran; leave it queued for a retry
        logger.info("[%s] find_email: lookup unavailable for %s — leaving queued", campaign, public_id)


def _resolve_email(session, lead) -> str:
    """Resolve a work email — free hub cache first, paid BetterContact second.

    Returns ``"hit"`` (``api_email`` set), ``"miss"`` (a finder ran and found
    nothing), or ``"unavailable"`` (no finder could run — no key, out of credits,
    or the service was unreachable). A fresh BetterContact hit is given back to
    the hub (moment 1); a cached hub hit is not re-contributed.
    """
    from openoutreach.contacts import service as contacts
    from openoutreach.emails import bettercontact

    cached_email = contacts.resolve(lead)  # free hub lookup
    if cached_email:
        lead.api_email = cached_email
        lead.save(update_fields=["api_email"])
        return "hit"

    if not bettercontact.is_configured():
        return "unavailable"

    already_resolved = bool(lead.api_email)
    outcome = lead.resolve_api_email()  # tri-state: True / False / None
    if outcome is True:
        if not already_resolved:
            contacts.contribute(session, lead, [lead.api_email], contacts.ORIGIN_BETTERCONTACT)
        return "hit"
    if outcome is False:
        return "miss"
    return "unavailable"
