import uuid

from linkedin.enums import ProfileState


def _make_ticket() -> str:
    """Generate a unique 16-char ticket for a Deal."""
    return uuid.uuid4().hex[:16]


def _get_stage(state: ProfileState, campaign):
    from crm.models import Stage
    return Stage.objects.get(name=state.value, department=campaign.department)


def _get_lead_source(campaign):
    from crm.models import LeadSource
    return LeadSource.objects.get(name="LinkedIn Scraper", department=campaign.department)
