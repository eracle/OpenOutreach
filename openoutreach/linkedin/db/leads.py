import logging

from django.db import transaction

from openoutreach.crm.models import DealState

logger = logging.getLogger(__name__)


@transaction.atomic
def promote_lead_to_deal(session, public_id: str, reason: str = ""):
    """Create a QUALIFIED Deal for a Lead.

    Returns the Deal.
    """
    from openoutreach.crm.models import Lead, Deal

    lead = Lead.objects.filter(public_identifier=public_id).first()
    if not lead:
        raise ValueError(f"No Lead for {public_id}")

    deal = Deal.objects.create(
        lead=lead,
        campaign=session.campaign,
        state=DealState.QUALIFIED,
        reason=reason,
    )

    from termcolor import colored
    logger.info("%s %s", public_id, colored("QUALIFIED", "green", attrs=["bold"]))
    return deal


def get_leads_for_qualification(session) -> list:
    """Leads eligible for qualification in the current campaign.

    Returns profile dicts for leads that are not permanently disqualified
    and have no Deal in this campaign.
    """
    from openoutreach.crm.models import Lead

    # Invariant (convention, not DB-enforced): a disqualified lead is never given
    # a NEW deal. It may still hold a terminal FAILED deal. Every deal-creating
    # query must filter disqualified=False to uphold this.
    leads = Lead.objects.filter(
        disqualified=False,
    ).exclude(
        deal__campaign=session.campaign,
    )

    return [lead.to_profile_dict() for lead in leads]


def disqualify_lead(public_id: str):
    """Set Lead.disqualified = True (account-level, permanent, cross-campaign)."""
    from openoutreach.crm.models import Lead

    lead = Lead.objects.filter(public_identifier=public_id).first()
    if not lead:
        logger.warning("disqualify_lead: no Lead for %s", public_id)
        return
    lead.disqualified = True
    lead.save(update_fields=["disqualified"])
