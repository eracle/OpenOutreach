import pytest

from linkedin.enums import ProfileState
from linkedin.setup.seeds import create_seed_leads_from_csv


@pytest.mark.django_db
def test_create_seed_leads_from_csv_supports_ready_to_connect(fake_session):
    from crm.models import Deal

    leads = [
        {
            "url": "https://www.linkedin.com/in/alice/",
            "public_id": "alice",
            "first_name": "Alice",
            "last_name": "Smith",
            "company_name": "Acme",
        },
    ]

    created = create_seed_leads_from_csv(
        fake_session.campaign,
        leads,
        initial_state=ProfileState.READY_TO_CONNECT,
    )

    assert created == 1
    deal = Deal.objects.get(
        lead__linkedin_url="https://www.linkedin.com/in/alice/",
        campaign=fake_session.campaign,
    )
    assert deal.state == ProfileState.READY_TO_CONNECT
