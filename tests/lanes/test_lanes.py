# tests/lanes/test_lanes.py
import pytest
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

from linkedin.db.crm_profiles import (
    get_profile,
    set_profile_state,
    save_scraped_profile,
)
from linkedin.lanes.enrich import EnrichLane, is_preexisting_connection
from linkedin.lanes.connect import ConnectLane
from linkedin.lanes.check_pending import CheckPendingLane
from linkedin.lanes.follow_up import FollowUpLane
from linkedin.ml.scorer import ProfileScorer
from linkedin.navigation.enums import ProfileState, MessageStatus
from linkedin.navigation.exceptions import SkipProfile, ReachedConnectionLimit
from linkedin.rate_limiter import RateLimiter


SAMPLE_PROFILE = {
    "first_name": "Alice",
    "last_name": "Smith",
    "headline": "Engineer",
    "positions": [{"company_name": "Acme"}],
}


def _make_enriched(session, public_id="alice"):
    """Create a Lead+Deal at ENRICHED with profile data."""
    url = f"https://www.linkedin.com/in/{public_id}/"
    save_scraped_profile(session, url, SAMPLE_PROFILE, None)


def _make_old_deal(session, days):
    """Set next_step_date to `days` ago on the user's first deal."""
    from crm.models import Deal

    deal = Deal.objects.filter(owner=session.django_user).first()
    deal.next_step_date = date.today() - timedelta(days=days)
    deal.save()


# ── Existing can_execute tests ────────────────────────────────────────


@pytest.mark.django_db
class TestEnrichLaneCanExecute:
    def test_can_execute_with_discovered(self, fake_session):
        set_profile_state(fake_session, "alice", ProfileState.DISCOVERED.value)
        lane = EnrichLane(fake_session)
        assert lane.can_execute() is True

    def test_cannot_execute_empty(self, fake_session):
        lane = EnrichLane(fake_session)
        assert lane.can_execute() is False

    def test_cannot_execute_only_enriched(self, fake_session):
        set_profile_state(fake_session, "alice", ProfileState.ENRICHED.value)
        lane = EnrichLane(fake_session)
        assert lane.can_execute() is False


@pytest.mark.django_db
class TestConnectLaneCanExecute:
    def test_can_execute_with_enriched_and_rate_ok(self, fake_session):
        set_profile_state(fake_session, "alice", ProfileState.ENRICHED.value)
        rl = RateLimiter(daily_limit=10)
        scorer = ProfileScorer(seed=42)
        lane = ConnectLane(fake_session, rl, scorer)
        assert lane.can_execute() is True

    def test_cannot_execute_rate_limited(self, fake_session):
        set_profile_state(fake_session, "alice", ProfileState.ENRICHED.value)
        rl = RateLimiter(daily_limit=0)
        scorer = ProfileScorer(seed=42)
        lane = ConnectLane(fake_session, rl, scorer)
        assert lane.can_execute() is False

    def test_cannot_execute_no_enriched(self, fake_session):
        rl = RateLimiter(daily_limit=10)
        scorer = ProfileScorer(seed=42)
        lane = ConnectLane(fake_session, rl, scorer)
        assert lane.can_execute() is False


@pytest.mark.django_db
class TestCheckPendingLaneCanExecute:
    def test_can_execute_with_old_pending(self, fake_session):
        set_profile_state(fake_session, "alice", ProfileState.PENDING.value)
        from crm.models import Deal
        deal = Deal.objects.filter(owner=fake_session.django_user).first()
        deal.next_step_date = date.today() - timedelta(days=5)
        deal.save()

        scorer = ProfileScorer(seed=42)
        lane = CheckPendingLane(fake_session, min_age_days=3, scorer=scorer)
        assert lane.can_execute() is True

    def test_cannot_execute_too_recent(self, fake_session):
        set_profile_state(fake_session, "alice", ProfileState.PENDING.value)
        # next_step_date is already date.today() from set_profile_state
        scorer = ProfileScorer(seed=42)
        lane = CheckPendingLane(fake_session, min_age_days=3, scorer=scorer)
        assert lane.can_execute() is False

    def test_cannot_execute_empty(self, fake_session):
        scorer = ProfileScorer(seed=42)
        lane = CheckPendingLane(fake_session, min_age_days=3, scorer=scorer)
        assert lane.can_execute() is False


@pytest.mark.django_db
class TestFollowUpLaneCanExecute:
    def test_can_execute_with_old_connected(self, fake_session):
        _make_enriched(fake_session)
        set_profile_state(fake_session, "alice", ProfileState.CONNECTED.value)
        _make_old_deal(fake_session, days=3)

        rl = RateLimiter(daily_limit=10)
        lane = FollowUpLane(fake_session, rl, min_age_days=1)
        assert lane.can_execute() is True

    def test_cannot_execute_rate_limited(self, fake_session):
        _make_enriched(fake_session)
        set_profile_state(fake_session, "alice", ProfileState.CONNECTED.value)
        _make_old_deal(fake_session, days=3)

        rl = RateLimiter(daily_limit=0)
        lane = FollowUpLane(fake_session, rl, min_age_days=1)
        assert lane.can_execute() is False

    def test_cannot_execute_empty(self, fake_session):
        rl = RateLimiter(daily_limit=10)
        lane = FollowUpLane(fake_session, rl, min_age_days=1)
        assert lane.can_execute() is False


# ── EnrichLane.execute() tests ────────────────────────────────────────


@pytest.mark.django_db
class TestEnrichLaneExecute:
    def _run(self, fake_session, get_profile_rv):
        """Create a DISCOVERED profile, mock the API, and run execute()."""
        set_profile_state(fake_session, "alice", ProfileState.DISCOVERED.value)

        with patch("linkedin.lanes.enrich.PlaywrightLinkedinAPI") as MockAPI:
            mock_api = MockAPI.return_value
            mock_api.get_profile.return_value = get_profile_rv
            lane = EnrichLane(fake_session)
            lane.execute()

    def test_execute_enriches_and_saves(self, fake_session):
        profile = {**SAMPLE_PROFILE, "connection_degree": 2}
        self._run(fake_session, (profile, {"raw": "data"}))
        result = get_profile(fake_session, "alice")
        assert result["state"] == ProfileState.ENRICHED.value

    def test_execute_marks_failed_on_none_profile(self, fake_session):
        self._run(fake_session, (None, None))
        result = get_profile(fake_session, "alice")
        assert result["state"] == ProfileState.FAILED.value

    def test_execute_marks_ignored_preexisting(self, fake_session):
        profile = {**SAMPLE_PROFILE, "connection_degree": 1}
        with patch.dict(
            "linkedin.lanes.enrich.CAMPAIGN_CONFIG",
            {"follow_up_existing_connections": False},
        ):
            self._run(fake_session, (profile, {"raw": "data"}))
        result = get_profile(fake_session, "alice")
        assert result["state"] == ProfileState.IGNORED.value

    def test_execute_marks_failed_on_exception(self, fake_session):
        set_profile_state(fake_session, "alice", ProfileState.DISCOVERED.value)

        with patch("linkedin.lanes.enrich.PlaywrightLinkedinAPI") as MockAPI:
            mock_api = MockAPI.return_value
            mock_api.get_profile.side_effect = RuntimeError("network error")
            lane = EnrichLane(fake_session)
            lane.execute()

        result = get_profile(fake_session, "alice")
        assert result["state"] == ProfileState.FAILED.value

    def test_execute_noop_when_no_urls(self, fake_session):
        # No DISCOVERED profiles → execute returns without error
        lane = EnrichLane(fake_session)
        lane.execute()  # should not raise


# ── ConnectLane.execute() tests ───────────────────────────────────────


@pytest.mark.django_db
class TestConnectLaneExecute:
    def _setup(self, fake_session):
        _make_enriched(fake_session)
        rl = RateLimiter(daily_limit=10)
        scorer = ProfileScorer(seed=42)
        return ConnectLane(fake_session, rl, scorer)

    @patch("linkedin.actions.connect.send_connection_request")
    @patch("linkedin.actions.connection_status.get_connection_status")
    def test_execute_sends_connection_and_records(
        self, mock_status, mock_send, fake_session
    ):
        mock_status.return_value = ProfileState.ENRICHED
        mock_send.return_value = ProfileState.PENDING

        lane = self._setup(fake_session)
        lane.execute()

        result = get_profile(fake_session, "alice")
        assert result["state"] == ProfileState.PENDING.value
        assert lane.rate_limiter._daily_count == 1

    @patch("linkedin.actions.connection_status.get_connection_status")
    def test_execute_ignores_preexisting_connected(
        self, mock_status, fake_session
    ):
        mock_status.return_value = ProfileState.CONNECTED

        with patch.dict(
            "linkedin.lanes.connect.CAMPAIGN_CONFIG",
            {"follow_up_existing_connections": False},
        ):
            lane = self._setup(fake_session)
            lane.execute()

        result = get_profile(fake_session, "alice")
        assert result["state"] == ProfileState.IGNORED.value

    @patch("linkedin.actions.connection_status.get_connection_status")
    def test_execute_follows_up_preexisting_connected(
        self, mock_status, fake_session
    ):
        mock_status.return_value = ProfileState.CONNECTED

        with patch.dict(
            "linkedin.lanes.connect.CAMPAIGN_CONFIG",
            {"follow_up_existing_connections": True},
        ):
            lane = self._setup(fake_session)
            lane.execute()

        result = get_profile(fake_session, "alice")
        assert result["state"] == ProfileState.CONNECTED.value

    @patch("linkedin.actions.connection_status.get_connection_status")
    def test_execute_detects_already_pending(
        self, mock_status, fake_session
    ):
        mock_status.return_value = ProfileState.PENDING
        lane = self._setup(fake_session)
        lane.execute()

        result = get_profile(fake_session, "alice")
        assert result["state"] == ProfileState.PENDING.value

    @patch("linkedin.actions.connection_status.get_connection_status")
    def test_execute_handles_rate_limit_exception(
        self, mock_status, fake_session
    ):
        mock_status.side_effect = ReachedConnectionLimit("weekly limit")
        lane = self._setup(fake_session)
        lane.execute()

        assert lane.rate_limiter._daily_exhausted is True

    @patch("linkedin.actions.connect.send_connection_request")
    @patch("linkedin.actions.connection_status.get_connection_status")
    def test_execute_handles_skip_profile(
        self, mock_status, mock_send, fake_session
    ):
        mock_status.return_value = ProfileState.ENRICHED
        mock_send.side_effect = SkipProfile("bad profile")

        lane = self._setup(fake_session)
        lane.execute()

        result = get_profile(fake_session, "alice")
        assert result["state"] == ProfileState.FAILED.value


# ── CheckPendingLane.execute() tests ──────────────────────────────────


@pytest.mark.django_db
class TestCheckPendingLaneExecute:
    def _setup(self, fake_session):
        _make_enriched(fake_session)
        set_profile_state(fake_session, "alice", ProfileState.PENDING.value)
        _make_old_deal(fake_session, days=5)

        scorer = ProfileScorer(seed=42)
        return CheckPendingLane(fake_session, min_age_days=3, scorer=scorer)

    @patch.object(CheckPendingLane, "_retrain")
    @patch("linkedin.actions.connection_status.get_connection_status")
    def test_execute_updates_state_from_connection_status(
        self, mock_status, mock_retrain, fake_session
    ):
        mock_status.return_value = ProfileState.CONNECTED
        lane = self._setup(fake_session)
        lane.execute()

        result = get_profile(fake_session, "alice")
        assert result["state"] == ProfileState.CONNECTED.value
        mock_retrain.assert_called_once()

    @patch.object(CheckPendingLane, "_retrain")
    @patch("linkedin.actions.connection_status.get_connection_status")
    def test_execute_no_retrain_when_no_flip(
        self, mock_status, mock_retrain, fake_session
    ):
        mock_status.return_value = ProfileState.PENDING
        lane = self._setup(fake_session)
        lane.execute()

        result = get_profile(fake_session, "alice")
        assert result["state"] == ProfileState.PENDING.value
        mock_retrain.assert_not_called()

    @patch.object(CheckPendingLane, "_retrain")
    @patch("linkedin.actions.connection_status.get_connection_status")
    def test_execute_noop_when_no_profiles(
        self, mock_status, mock_retrain, fake_session
    ):
        # No pending profiles → execute returns immediately
        scorer = ProfileScorer(seed=42)
        lane = CheckPendingLane(fake_session, min_age_days=3, scorer=scorer)
        lane.execute()

        mock_status.assert_not_called()
        mock_retrain.assert_not_called()


# ── FollowUpLane.execute() tests ─────────────────────────────────────


@pytest.mark.django_db
class TestFollowUpLaneExecute:
    def _setup(self, fake_session):
        _make_enriched(fake_session)
        set_profile_state(fake_session, "alice", ProfileState.CONNECTED.value)
        _make_old_deal(fake_session, days=3)

        rl = RateLimiter(daily_limit=10)
        return FollowUpLane(fake_session, rl, min_age_days=1)

    @patch("linkedin.actions.message.send_follow_up_message")
    def test_execute_sends_message_and_completes(
        self, mock_send, fake_session
    ):
        mock_send.return_value = MessageStatus.SENT
        lane = self._setup(fake_session)
        lane.execute()

        result = get_profile(fake_session, "alice")
        assert result["state"] == ProfileState.COMPLETED.value
        assert lane.rate_limiter._daily_count == 1

    @patch("linkedin.actions.message.send_follow_up_message")
    def test_execute_skipped_message_stays_connected(
        self, mock_send, fake_session
    ):
        mock_send.return_value = MessageStatus.SKIPPED
        lane = self._setup(fake_session)
        lane.execute()

        result = get_profile(fake_session, "alice")
        assert result["state"] == ProfileState.CONNECTED.value

    @patch("linkedin.actions.message.send_follow_up_message")
    def test_execute_noop_when_no_profiles(
        self, mock_send, fake_session
    ):
        # No connected profiles → execute returns immediately
        rl = RateLimiter(daily_limit=10)
        lane = FollowUpLane(fake_session, rl, min_age_days=1)
        lane.execute()

        mock_send.assert_not_called()


# ── is_preexisting_connection() tests ─────────────────────────────────


class TestIsPreexistingConnection:
    def test_degree_1_is_preexisting(self):
        with patch.dict(
            "linkedin.lanes.enrich.CAMPAIGN_CONFIG",
            {"follow_up_existing_connections": False},
        ):
            assert is_preexisting_connection({"connection_degree": 1}) is True

    def test_degree_2_not_preexisting(self):
        with patch.dict(
            "linkedin.lanes.enrich.CAMPAIGN_CONFIG",
            {"follow_up_existing_connections": False},
        ):
            assert is_preexisting_connection({"connection_degree": 2}) is False

    def test_follow_up_flag_overrides(self):
        with patch.dict(
            "linkedin.lanes.enrich.CAMPAIGN_CONFIG",
            {"follow_up_existing_connections": True},
        ):
            assert is_preexisting_connection({"connection_degree": 1}) is False

    def test_none_degree_not_preexisting(self):
        with patch.dict(
            "linkedin.lanes.enrich.CAMPAIGN_CONFIG",
            {"follow_up_existing_connections": False},
        ):
            assert is_preexisting_connection({"connection_degree": None}) is False
