# tests/conftest.py
from unittest.mock import patch

import numpy as np
import pytest

from openoutreach.core.management.setup_crm import setup_crm
from tests.factories import UserFactory


@pytest.fixture(autouse=True)
def _ensure_crm_data(db):
    """
    Ensure CRM bootstrap data exists before every test.
    Uses `db` fixture (not transactional_db) for compatibility.
    Since transaction=True tests rollback, we re-create data each time.
    """
    setup_crm()


@pytest.fixture(autouse=True)
def _mock_embeddings(request):
    """Stub fastembed so tests don't need the ONNX model."""
    if "no_embed_mock" in request.keywords:
        yield
    else:
        with patch("openoutreach.core.ml.embeddings.embed_text", return_value=np.ones(384)):
            yield


class FakeAccountSession:
    """Minimal stand-in for AccountSession — the Django User + SiteConfig identity.

    ``self_profile`` is the OPERATOR's identity (still a plain dict, used by the
    contacts give-back and the summary/agent prompt builders). Leads carry their
    own ``profile_url`` identity on the model — the session no longer holds a
    LinkedInProfile row.
    """

    def __init__(self, django_user, campaign):
        self.django_user = django_user
        self.campaign = campaign
        self.self_profile = {
            "public_identifier": django_user.email or django_user.username,
            "first_name": "Diego",
            "last_name": "Ramirez",
            "urn": "urn:li:fsd_profile:TEST",
        }
        # Resolved post-login on the real session; None here → no active-hours
        # gating (planner tests disable active hours regardless).
        self.active_timezone = None

    @property
    def campaigns(self):
        from openoutreach.core.models import Campaign
        return Campaign.objects.filter(users=self.django_user)

    def ensure_browser(self):
        pass


@pytest.fixture
def fake_session(db):
    """An AccountSession-like object backed by the Django test DB."""
    from openoutreach.core.models import Campaign

    user = UserFactory(username="testuser", email="testuser@example.com")

    campaign = Campaign.objects.first()
    if campaign is None:
        campaign = Campaign.objects.create(name="LinkedIn Outreach")
    campaign.users.add(user)

    return FakeAccountSession(django_user=user, campaign=campaign)
