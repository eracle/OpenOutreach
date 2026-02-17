# tests/lanes/test_lanes.py
import pytest
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.utils import timezone

from linkedin.db.crm_profiles import (
    get_profile,
    set_profile_state,
    create_enriched_lead,
    promote_lead_to_contact,
    count_qualified_profiles,
)
from linkedin.lanes.connect import ConnectLane
from linkedin.lanes.check_pending import CheckPendingLane
from linkedin.lanes.follow_up import FollowUpLane
from linkedin.ml.qualifier import BayesianQualifier
from linkedin.navigation.enums import ProfileState
from linkedin.navigation.exceptions import SkipProfile, ReachedConnectionLimit
from linkedin.rate_limiter import RateLimiter


SAMPLE_PROFILE = {
    "first_name": "Alice",
    "last_name": "Smith",
    "headline": "Engineer",
    "positions": [{"company_name": "Acme"}],
}


def _make_qualified(session, public_id="alice"):
    """Create a Lead + Contact + Deal at 'New' stage."""
    url = f"https://www.linkedin.com/in/{public_id}/"
    create_enriched_lead(session, url, SAMPLE_PROFILE)
    promote_lead_to_contact(session, public_id)


def _make_connected(session, public_id="alice"):
    """Create a Lead + Contact + Deal at 'Connected' stage."""
    _make_qualified(session, public_id)
    set_profile_state(session, public_id, ProfileState.CONNECTED.value)


def _make_old_deal(session, days):
    """Set update_date to `days` ago on the user's first deal (bypasses auto_now)."""
    from crm.models import Deal

    deal = Deal.objects.filter(owner=session.django_user).first()
    Deal.objects.filter(pk=deal.pk).update(
        update_date=timezone.now() - timedelta(days=days)
    )


# ── ConnectLane tests ────────────────────────────────────────


@pytest.mark.django_db
class TestConnectLaneCanExecute:
    def test_can_execute_with_qualified_and_rate_ok(self, fake_session):
        _make_qualified(fake_session)
        rl = RateLimiter(daily_limit=10)
        scorer = BayesianQualifier(seed=42)
        lane = ConnectLane(fake_session, rl, scorer)
        assert lane.can_execute() is True

    def test_cannot_execute_rate_limited(self, fake_session):
        _make_qualified(fake_session)
        rl = RateLimiter(daily_limit=0)
        scorer = BayesianQualifier(seed=42)
        lane = ConnectLane(fake_session, rl, scorer)
        assert lane.can_execute() is False

    def test_cannot_execute_no_qualified(self, fake_session):
        rl = RateLimiter(daily_limit=10)
        scorer = BayesianQualifier(seed=42)
        lane = ConnectLane(fake_session, rl, scorer)
        assert lane.can_execute() is False

    def test_cannot_execute_only_enriched_lead(self, fake_session):
        """Enriched leads (no Deal) should NOT be picked up by connect lane."""
        create_enriched_lead(
            fake_session,
            "https://www.linkedin.com/in/alice/",
            SAMPLE_PROFILE,
        )
        rl = RateLimiter(daily_limit=10)
        scorer = BayesianQualifier(seed=42)
        lane = ConnectLane(fake_session, rl, scorer)
        assert lane.can_execute() is False


@pytest.mark.django_db
class TestConnectLaneExecute:
    @pytest.fixture(autouse=True)
    def _db(self, embeddings_db):
        pass

    def _setup(self, fake_session):
        _make_qualified(fake_session)
        rl = RateLimiter(daily_limit=10)
        scorer = BayesianQualifier(seed=42)
        return ConnectLane(fake_session, rl, scorer)

    @patch("linkedin.actions.connect.send_connection_request")
    @patch("linkedin.actions.connection_status.get_connection_status")
    def test_execute_sends_connection_and_records(
        self, mock_status, mock_send, fake_session
    ):
        mock_status.return_value = ProfileState.NEW
        mock_send.return_value = ProfileState.PENDING

        lane = self._setup(fake_session)
        lane.execute()

        result = get_profile(fake_session, "alice")
        assert result["state"] == ProfileState.PENDING.value
        assert lane.rate_limiter._daily_count == 1

    @patch("linkedin.actions.connection_status.get_connection_status")
    def test_execute_marks_preexisting_connected(
        self, mock_status, fake_session
    ):
        """Pre-existing connections are always marked CONNECTED."""
        mock_status.return_value = ProfileState.CONNECTED

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
        mock_status.return_value = ProfileState.NEW
        mock_send.side_effect = SkipProfile("bad profile")

        lane = self._setup(fake_session)
        lane.execute()

        result = get_profile(fake_session, "alice")
        assert result["state"] == ProfileState.FAILED.value


# ── CheckPendingLane tests ──────────────────────────────────────


@pytest.mark.django_db
class TestCheckPendingLaneCanExecute:
    def _make_pending(self, session, public_id="alice"):
        _make_qualified(session, public_id)
        set_profile_state(session, public_id, ProfileState.PENDING.value)

    def test_can_execute_with_old_pending(self, fake_session):
        self._make_pending(fake_session)
        from crm.models import Deal
        deal = Deal.objects.filter(owner=fake_session.django_user).first()
        Deal.objects.filter(pk=deal.pk).update(
            update_date=timezone.now() - timedelta(days=5)
        )

        lane = CheckPendingLane(fake_session, recheck_after_hours=72)
        assert lane.can_execute() is True

    def test_cannot_execute_too_recent(self, fake_session):
        self._make_pending(fake_session)
        lane = CheckPendingLane(fake_session, recheck_after_hours=72)
        assert lane.can_execute() is False

    def test_cannot_execute_with_high_backoff(self, fake_session):
        import json
        self._make_pending(fake_session)
        from crm.models import Deal
        deal = Deal.objects.filter(owner=fake_session.django_user).first()
        Deal.objects.filter(pk=deal.pk).update(
            update_date=timezone.now() - timedelta(hours=5),
            next_step=json.dumps({"backoff_hours": 100}),
        )

        lane = CheckPendingLane(fake_session, recheck_after_hours=1)
        assert lane.can_execute() is False

    def test_cannot_execute_empty(self, fake_session):
        lane = CheckPendingLane(fake_session, recheck_after_hours=72)
        assert lane.can_execute() is False


@pytest.mark.django_db
class TestCheckPendingLaneExecute:
    @pytest.fixture(autouse=True)
    def _db(self, embeddings_db):
        pass

    def _setup(self, fake_session):
        _make_qualified(fake_session)
        set_profile_state(fake_session, "alice", ProfileState.PENDING.value)
        _make_old_deal(fake_session, days=5)

        return CheckPendingLane(fake_session, recheck_after_hours=72)

    @patch("linkedin.actions.connection_status.get_connection_status")
    def test_execute_updates_state_from_connection_status(
        self, mock_status, fake_session
    ):
        mock_status.return_value = ProfileState.CONNECTED
        lane = self._setup(fake_session)
        lane.execute()

        result = get_profile(fake_session, "alice")
        assert result["state"] == ProfileState.CONNECTED.value

    @patch("linkedin.actions.connection_status.get_connection_status")
    def test_execute_stays_pending(
        self, mock_status, fake_session
    ):
        mock_status.return_value = ProfileState.PENDING
        lane = self._setup(fake_session)
        lane.execute()

        result = get_profile(fake_session, "alice")
        assert result["state"] == ProfileState.PENDING.value

    @patch("linkedin.actions.connection_status.get_connection_status")
    def test_execute_doubles_backoff_when_still_pending(
        self, mock_status, fake_session
    ):
        import json
        mock_status.return_value = ProfileState.PENDING
        lane = self._setup(fake_session)
        lane.execute()

        from crm.models import Deal
        from linkedin.db.crm_profiles import public_id_to_url
        deal = Deal.objects.get(lead__website=public_id_to_url("alice"))
        meta = json.loads(deal.next_step)
        # Default base is 72h, doubled to 144h
        assert meta["backoff_hours"] == 144

    @patch("linkedin.actions.connection_status.get_connection_status")
    def test_execute_doubles_existing_backoff(
        self, mock_status, fake_session
    ):
        import json
        mock_status.return_value = ProfileState.PENDING

        _make_qualified(fake_session, "bob")
        set_profile_state(fake_session, "bob", ProfileState.PENDING.value)
        from crm.models import Deal
        from linkedin.db.crm_profiles import public_id_to_url
        Deal.objects.filter(lead__website=public_id_to_url("bob")).update(
            update_date=timezone.now() - timedelta(days=5),
            next_step=json.dumps({"backoff_hours": 10}),
        )

        lane = CheckPendingLane(fake_session, recheck_after_hours=72)
        lane.execute()

        deal = Deal.objects.get(lead__website=public_id_to_url("bob"))
        meta = json.loads(deal.next_step)
        assert meta["backoff_hours"] == 20

    @patch("linkedin.actions.connection_status.get_connection_status")
    def test_execute_noop_when_no_profiles(
        self, mock_status, fake_session
    ):
        lane = CheckPendingLane(fake_session, recheck_after_hours=72)
        lane.execute()

        mock_status.assert_not_called()


# ── FollowUpLane tests ─────────────────────────────────────


@pytest.mark.django_db
class TestFollowUpLaneCanExecute:
    def test_can_execute_with_connected(self, fake_session):
        _make_connected(fake_session)

        rl = RateLimiter(daily_limit=10)
        lane = FollowUpLane(fake_session, rl)
        assert lane.can_execute() is True

    def test_cannot_execute_rate_limited(self, fake_session):
        _make_connected(fake_session)

        rl = RateLimiter(daily_limit=0)
        lane = FollowUpLane(fake_session, rl)
        assert lane.can_execute() is False

    def test_cannot_execute_empty(self, fake_session):
        rl = RateLimiter(daily_limit=10)
        lane = FollowUpLane(fake_session, rl)
        assert lane.can_execute() is False


@pytest.mark.django_db
class TestFollowUpLaneExecute:
    def _setup(self, fake_session):
        _make_connected(fake_session)

        rl = RateLimiter(daily_limit=10)
        return FollowUpLane(fake_session, rl)

    @patch("linkedin.actions.message.send_follow_up_message")
    def test_execute_sends_message_and_completes(
        self, mock_send, fake_session
    ):
        mock_send.return_value = "Hello Alice!"
        lane = self._setup(fake_session)
        lane.execute()

        result = get_profile(fake_session, "alice")
        assert result["state"] == ProfileState.COMPLETED.value
        assert lane.rate_limiter._daily_count == 1

    @patch("linkedin.actions.message.send_follow_up_message")
    def test_execute_saves_chat_message(
        self, mock_send, fake_session
    ):
        from chat.models import ChatMessage
        from django.contrib.contenttypes.models import ContentType
        from crm.models import Lead

        mock_send.return_value = "Hello Alice!"
        lane = self._setup(fake_session)
        lane.execute()

        lead = Lead.objects.get(website="https://www.linkedin.com/in/alice/")
        ct = ContentType.objects.get_for_model(lead)
        msg = ChatMessage.objects.get(content_type=ct, object_id=lead.pk)
        assert msg.content == "Hello Alice!"
        assert msg.owner == fake_session.django_user

    @patch("linkedin.actions.message.send_follow_up_message")
    def test_execute_skipped_message_stays_connected(
        self, mock_send, fake_session
    ):
        mock_send.return_value = None
        lane = self._setup(fake_session)
        lane.execute()

        result = get_profile(fake_session, "alice")
        assert result["state"] == ProfileState.CONNECTED.value

    @patch("linkedin.actions.message.send_follow_up_message")
    def test_execute_skipped_message_no_chat_saved(
        self, mock_send, fake_session
    ):
        from chat.models import ChatMessage

        mock_send.return_value = None
        lane = self._setup(fake_session)
        lane.execute()

        assert ChatMessage.objects.count() == 0

    @patch("linkedin.actions.message.send_follow_up_message")
    def test_execute_noop_when_no_profiles(
        self, mock_send, fake_session
    ):
        rl = RateLimiter(daily_limit=10)
        lane = FollowUpLane(fake_session, rl)
        lane.execute()

        mock_send.assert_not_called()
