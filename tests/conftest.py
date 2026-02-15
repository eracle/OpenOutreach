# tests/conftest.py
from unittest.mock import patch

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
    """Minimal stand-in for AccountSession — exposes django_user."""

    def __init__(self, django_user):
        self.django_user = django_user
        self.handle = django_user.username

    def ensure_browser(self):
        pass


@pytest.fixture
def fake_session(db):
    """An AccountSession-like object backed by the Django test DB."""
    from common.models import Department

    user = UserFactory(username="testuser")
    dept = Department.objects.get(name="LinkedIn Outreach")
    if dept not in user.groups.all():
        user.groups.add(dept)
    return FakeAccountSession(django_user=user)


@pytest.fixture
def embeddings_db(tmp_path):
    """A fresh, initialized embeddings DB for tests that need it."""
    import duckdb

    db_path = tmp_path / "embeddings.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("""
        CREATE TABLE IF NOT EXISTS profile_embeddings (
            lead_id INTEGER PRIMARY KEY,
            public_identifier VARCHAR NOT NULL,
            embedding FLOAT[384] NOT NULL,
            is_seed BOOLEAN DEFAULT FALSE,
            label INTEGER,
            llm_reason VARCHAR,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            labeled_at TIMESTAMP
        )
    """)
    con.close()
    with patch("linkedin.ml.embeddings.EMBEDDINGS_DB", db_path):
        yield db_path
