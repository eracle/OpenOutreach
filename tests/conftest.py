# tests/conftest.py
import pytest
from django.contrib.auth.models import Group

from linkedin.management.setup_crm import setup_crm
from tests.factories import UserFactory


@pytest.fixture(autouse=True)
def _ensure_crm_data(db):
    """
    Ensure CRM bootstrap data exists before every test.
    Uses `db` fixture (not transactional_db) for compatibility.
    Since transaction=True tests rollback, we re-create data each time.
    """
    # DjangoCRM's user creation signal expects "co-workers" group
    Group.objects.get_or_create(name="co-workers")
    setup_crm()


class FakeAccountSession:
    """Minimal stand-in for AccountSession — exposes django_user + campaign."""

    def __init__(self, django_user, linkedin_profile, campaign):
        self.django_user = django_user
        self.handle = django_user.username
        self.linkedin_profile = linkedin_profile
        self.campaign = campaign
        self.account_cfg = {
            "handle": self.handle,
            "username": linkedin_profile.linkedin_username,
            "password": linkedin_profile.linkedin_password,
            "subscribe_newsletter": linkedin_profile.subscribe_newsletter,
            "active": linkedin_profile.active,
        }

    @property
    def campaigns(self):
        from linkedin.models import Campaign
        return Campaign.objects.filter(
            department__in=self.django_user.groups.all()
        ).select_related("department")

    def ensure_browser(self):
        pass


@pytest.fixture
def fake_session(db):
    """An AccountSession-like object backed by the Django test DB."""
    from common.models import Department
    from linkedin.models import Campaign, LinkedInProfile

    user = UserFactory(username="testuser")
    dept = Department.objects.get(name="LinkedIn Outreach")
    if dept not in user.groups.all():
        user.groups.add(dept)

    campaign = Campaign.objects.filter(department=dept).first()
    if campaign is None:
        campaign = Campaign.objects.create(department=dept)

    linkedin_profile, _ = LinkedInProfile.objects.get_or_create(
        user=user,
        defaults={
            "linkedin_username": "testuser@example.com",
            "linkedin_password": "testpass",
        },
    )

    return FakeAccountSession(django_user=user, linkedin_profile=linkedin_profile, campaign=campaign)


@pytest.fixture
def embeddings_db(db):
    """Marker fixture — embeddings now live in the Django test DB."""
    yield
