# tests/db/test_deals.py
"""State-transition logging in `core/db/deals.set_profile_state`.

The regression locked down here: a `no email` enrichment miss is a benign,
expected terminal outcome, so it has its own terminal state
(`NO_EMAIL_BETTERCONTACT`) that logs as a muted `NO EMAIL` — not the red `FAILED`
reserved for genuine failures.
"""
import logging

import numpy as np
import pytest

from openoutreach.core.db.deals import set_profile_state
from openoutreach.core.db.leads import promote_lead_to_deal
from openoutreach.crm.models import DealState


def _make_deal(session, slug="alice"):
    from openoutreach.crm.models import Lead

    url = f"https://www.linkedin.com/in/{slug}/"
    Lead.objects.create(
        profile_url=url,
        profile_text="engineer at acme",
        embedding=np.ones(384, dtype=np.float32).tobytes(),
    )
    promote_lead_to_deal(session, url)
    return url


@pytest.mark.django_db
def test_no_email_miss_logs_muted_not_failed(fake_session, caplog):
    url = _make_deal(fake_session)
    with caplog.at_level(logging.INFO, logger="openoutreach.core.db.deals"):
        set_profile_state(fake_session, url, DealState.NO_EMAIL_BETTERCONTACT.value)

    line = caplog.text
    assert "NO EMAIL" in line
    assert "FAILED" not in line


@pytest.mark.django_db
def test_true_failure_still_logs_failed(fake_session, caplog):
    url = _make_deal(fake_session)
    with caplog.at_level(logging.INFO, logger="openoutreach.core.db.deals"):
        set_profile_state(fake_session, url, DealState.FAILED.value, reason="wrong fit")

    assert "FAILED" in caplog.text
    assert "NO EMAIL" not in caplog.text
