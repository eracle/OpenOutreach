import uuid

from linkedin.enums import ProfileState


def _make_ticket() -> str:
    """Generate a unique 16-char ticket for a Deal."""
    return uuid.uuid4().hex[:16]


def _get_stage(state: ProfileState, session):
    from crm.models import Stage
    dept = session.campaign.department
    return Stage.objects.get(name=state.value, department=dept)


def _get_lead_source(session):
    from crm.models import LeadSource
    dept = session.campaign.department
    return LeadSource.objects.get(name="LinkedIn Scraper", department=dept)
