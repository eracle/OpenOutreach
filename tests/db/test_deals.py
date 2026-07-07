# tests/db/test_deals.py
"""State-transition logging in `core/db/deals.set_profile_state`.

The regression locked down here: a `no email` enrichment miss is a benign,
expected terminal outcome, so it must log as a muted `NO EMAIL` — not the red
`FAILED` reserved for genuine failures. The FSM state stays `FAILED` (the ML
labeler and pools depend on it); only the log rendering is softened.
"""
import logging

import numpy as np
import pytest

from openoutreach.core.db.deals import NO_EMAIL_REASON, set_profile_state
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
        set_profile_state(fake_session, url, DealState.FAILED.value, reason=NO_EMAIL_REASON)

    line = caplog.text
    assert "NO EMAIL" in line
    assert "FAILED" not in line
    # the reason is now redundant with the label, so it isn't repeated as a suffix
    assert "(no email)" not in line


@pytest.mark.django_db
def test_true_failure_still_logs_failed(fake_session, caplog):
    url = _make_deal(fake_session)
    with caplog.at_level(logging.INFO, logger="openoutreach.core.db.deals"):
        set_profile_state(fake_session, url, DealState.FAILED.value, reason="wrong fit")

    assert "FAILED" in caplog.text
    assert "NO EMAIL" not in caplog.text
