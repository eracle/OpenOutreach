# tests/conftest.py
from unittest.mock import patch

import numpy as np
import pytest
import requests

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
def _no_live_writes_to_our_own_services():
    """No test may write to the real hub or the real mailing list.

    Both are reached by *completing onboarding*, which many tests do incidentally on
    their way to something else: `_finalize_account` mints the operator's hub token
    and, on a yes, subscribes them to the newsletter. Unguarded, anyone's `make test`
    POSTs a fabricated operator into **production** — a service holding other
    people's contributions — and signs a fake address up to the list.

    Both callers are best-effort by design, so a refused connection is exactly the
    no-op they already handle. Tests that exercise either client patch the same
    target themselves and win, because their patch is applied inside this one.
    """
    refuse = requests.ConnectionError("no network in tests")
    with patch("openoutreach.contacts.service.requests.post", side_effect=refuse), \
         patch("openoutreach.core.newsletter.requests.post", side_effect=refuse):
        yield


@pytest.fixture(autouse=True)
def _mock_embeddings(request):
    """Stub fastembed so tests don't need the ONNX model."""
    if "no_embed_mock" in request.keywords:
        yield
    else:
        with patch("openoutreach.core.ml.embeddings.embed_text", return_value=np.ones(384)):
            yield


@pytest.fixture(autouse=True)
def _no_fit_survives_a_test():
    """``qualifier_for`` keeps the fitted model per campaign, keyed on the labels.

    That key cannot go stale inside a run, but a test database reuses primary keys, so
    two tests can be one campaign with one label set and different intent. Each test
    starts from an empty cache.
    """
    from openoutreach.core.ml.qualifier import _FITTED

    _FITTED.clear()
    yield
    _FITTED.clear()


@pytest.fixture
def operator(db):
    """The onboarded operator — what ``core.operator.get_active_user()`` will find."""
    return UserFactory(username="testuser", email="testuser@example.com")


@pytest.fixture
def campaign(db, operator):
    """The campaign under test, owned by the operator.

    Steps and pipeline functions take a campaign now; the operator is looked up
    (``core/operator.py``) rather than threaded through, so nothing carries a
    session object any more.
    """
    from openoutreach.core.models import Campaign

    row = Campaign.objects.first() or Campaign.objects.create(name="Email Outreach")
    row.users.add(operator)
    return row
