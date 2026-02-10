# tests/conftest.py
import pytest
from django.contrib.auth.models import User, Group

from linkedin.management.setup_crm import setup_crm


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
    """Minimal stand-in for AccountSession — exposes django_user."""

    def __init__(self, django_user):
        self.django_user = django_user
        self.handle = django_user.username


@pytest.fixture
def fake_session(db):
    """An AccountSession-like object backed by the Django test DB."""
    from common.models import Department

    # Ensure CRM data exists (co-workers group, department, stages, etc.)
    Group.objects.get_or_create(name="co-workers")
    setup_crm()

    user, _ = User.objects.get_or_create(
        username="testuser",
        defaults={"is_staff": True, "is_active": True},
    )
    dept = Department.objects.get(name="LinkedIn Outreach")
    if dept not in user.groups.all():
        user.groups.add(dept)
    return FakeAccountSession(django_user=user)
