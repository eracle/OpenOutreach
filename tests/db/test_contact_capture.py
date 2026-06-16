# tests/db/test_contact_capture.py
from unittest.mock import patch

import pytest

from openoutreach.core.db.deals import set_profile_state
from openoutreach.linkedin.db.leads import create_enriched_lead, promote_lead_to_deal
from linkedin_cli.enums import ProfileState
from linkedin_cli.exceptions import AuthenticationError, ProfileInaccessibleError

SAMPLE_PROFILE = {
    "first_name": "Alice",
    "last_name": "Smith",
    "headline": "Engineer",
    "positions": [{"company_name": "Acme"}],
    "urn": "urn:li:fsd_profile:ABC123",
}
CONTACT = {
    "email": "alice@acme.com",
    "emails": ["alice@acme.com"],
    "phone_numbers": ["+15551234567"],
}
EMPTY = {"email": None, "emails": [], "phone_numbers": []}


def _promote_alice(session):
    create_enriched_lead(session, "https://www.linkedin.com/in/alice/", SAMPLE_PROFILE)
    promote_lead_to_deal(session, "alice")


def _patch_api(get_contact_info=None, side_effect=None):
    """Patch the linkedin_cli boundary; returns the mocked get_contact_info."""
    patcher = patch("linkedin_cli.api.client.PlaywrightLinkedinAPI")
    mock_cls = patcher.start()
    method = mock_cls.return_value.get_contact_info
    if side_effect is not None:
        method.side_effect = side_effect
    else:
        method.return_value = (get_contact_info or CONTACT, "raw-rsc-text")
    return patcher, method


def _alice():
    from openoutreach.crm.models import Lead
    return Lead.objects.get(public_identifier="alice")


@pytest.mark.no_contact_capture_mock
@pytest.mark.django_db
class TestContactCaptureOnConnect:
    def test_connected_captures_and_persists(self, fake_session):
        _promote_alice(fake_session)
        patcher, method = _patch_api()
        try:
            set_profile_state(fake_session, "alice", ProfileState.CONNECTED.value)
        finally:
            patcher.stop()

        assert method.call_count == 1
        assert _alice().contact_info == CONTACT

    def test_non_connected_does_not_capture(self, fake_session):
        _promote_alice(fake_session)
        patcher, method = _patch_api()
        try:
            set_profile_state(fake_session, "alice", ProfileState.PENDING.value)
        finally:
            patcher.stop()

        assert method.call_count == 0
        assert _alice().contact_info is None

    def test_scrape_error_leaves_state_connected_and_field_null(self, fake_session):
        _promote_alice(fake_session)
        patcher, _ = _patch_api(side_effect=ProfileInaccessibleError("private"))
        try:
            # Must NOT raise — capture is best-effort.
            set_profile_state(fake_session, "alice", ProfileState.CONNECTED.value)
        finally:
            patcher.stop()

        from openoutreach.crm.models import Deal
        deal = Deal.objects.get(lead__public_identifier="alice")
        assert deal.state == ProfileState.CONNECTED
        assert _alice().contact_info is None

    def test_second_connected_does_not_rescrape(self, fake_session):
        _promote_alice(fake_session)
        patcher, method = _patch_api()
        try:
            set_profile_state(fake_session, "alice", ProfileState.CONNECTED.value)
            # Bounce away and back: the second CONNECTED is a real state change,
            # but contact_info is already set, so the accessor must not re-scrape.
            set_profile_state(fake_session, "alice", ProfileState.PENDING.value)
            set_profile_state(fake_session, "alice", ProfileState.CONNECTED.value)
        finally:
            patcher.stop()

        assert method.call_count == 1

    def test_authentication_error_propagates(self, fake_session):
        _promote_alice(fake_session)
        patcher, _ = _patch_api(side_effect=AuthenticationError("401"))
        try:
            with pytest.raises(AuthenticationError):
                set_profile_state(fake_session, "alice", ProfileState.CONNECTED.value)
        finally:
            patcher.stop()

        assert _alice().contact_info is None


@pytest.mark.no_contact_capture_mock
@pytest.mark.django_db
class TestContactCaptureRetrySentinel:
    def test_failed_read_stays_null_and_is_retried(self, fake_session):
        _promote_alice(fake_session)
        # First read raises (field stays None → retriable); the later visit succeeds.
        patcher, method = _patch_api(side_effect=[ProfileInaccessibleError("blip"), (CONTACT, "raw")])
        try:
            with pytest.raises(ProfileInaccessibleError):
                _alice().capture_contact_info(fake_session)
            assert _alice().contact_info is None
            _alice().capture_contact_info(fake_session)
        finally:
            patcher.stop()

        assert method.call_count == 2
        assert _alice().contact_info == CONTACT

    def test_clean_empty_is_not_rescraped(self, fake_session):
        _promote_alice(fake_session)
        # A successful read exposing no email is the "not exposed" sentinel — stop.
        patcher, method = _patch_api(get_contact_info=EMPTY)
        try:
            _alice().capture_contact_info(fake_session)
            assert _alice().contact_info == EMPTY
            _alice().capture_contact_info(fake_session)
        finally:
            patcher.stop()

        assert method.call_count == 1
