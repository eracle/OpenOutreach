# tests/lanes/test_lanes.py
import pytest
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.utils import timezone

from linkedin.db.crm_profiles import (
    set_profile_state,
    create_enriched_lead,
    promote_lead_to_contact,
    STATE_TO_STAGE,
)
from linkedin.lanes.connect import ConnectLane
from linkedin.lanes.check_pending import CheckPendingLane
from linkedin.lanes.follow_up import FollowUpLane
from linkedin.models import ActionLog
from linkedin.ml.qualifier import BayesianQualifier
from linkedin.navigation.enums import ProfileState
from linkedin.navigation.exceptions import SkipProfile, ReachedConnectionLimit


SAMPLE_PROFILE = {
    "first_name": "Alice",
    "last_name": "Smith",
    "headline": "Engineer",
    "positions": [{"company_name": "Acme"}],
}


def _assert_deal_state(session, public_id, expected_state: ProfileState):
    from crm.models import Deal
    deal = Deal.objects.get(
        lead__website=f"https://www.linkedin.com/in/{public_id}/",
        owner=session.django_user,
    )
    assert deal.stage.name == STATE_TO_STAGE[expected_state]


def _make_qualified(session, public_id="alice"):
    url = f"https://www.linkedin.com/in/{public_id}/"
    create_enriched_lead(session, url, SAMPLE_PROFILE)
    promote_lead_to_contact(session, public_id)


def _make_connected(session, public_id="alice"):
    _make_qualified(session, public_id)
    set_profile_state(session, public_id, ProfileState.CONNECTED.value)


def _make_old_deal(session, days):
    from crm.models import Deal
    deal = Deal.objects.filter(owner=session.django_user).first()
    Deal.objects.filter(pk=deal.pk).update(
        update_date=timezone.now() - timedelta(days=days)
    )


# ── ConnectLane tests ────────────────────────────────────────


@pytest.mark.django_db
class TestConnectLaneCanExecute:
    def test_can_execute_when_rate_ok(self, fake_session):
        lane = ConnectLane(fake_session, BayesianQualifier(seed=42))
        assert lane.can_execute() is True

    def test_cannot_execute_rate_limited(self, fake_session):
        fake_session.linkedin_profile.connect_daily_limit = 0
        fake_session.linkedin_profile.save(update_fields=["connect_daily_limit"])
        lane = ConnectLane(fake_session, BayesianQualifier(seed=42))
        assert lane.can_execute() is False


@pytest.mark.django_db
class TestConnectLaneExecute:
    @pytest.fixture(autouse=True)
    def _db(self, embeddings_db):
        pass

    def _lane(self, fake_session):
        _make_qualified(fake_session)
        scorer = BayesianQualifier(seed=42)
        scorer.rank_profiles = lambda profiles, **kw: profiles
        return ConnectLane(fake_session, scorer)

    @patch("linkedin.actions.connect.send_connection_request")
    @patch("linkedin.actions.connection_status.get_connection_status")
    def test_sends_connection_and_records(self, mock_status, mock_send, fake_session):
        mock_status.return_value = ProfileState.NEW
        mock_send.return_value = ProfileState.PENDING
        self._lane(fake_session).execute()
        _assert_deal_state(fake_session, "alice", ProfileState.PENDING)
        assert ActionLog.objects.filter(action_type=ActionLog.ActionType.CONNECT).count() == 1

    @patch("linkedin.actions.connection_status.get_connection_status")
    def test_marks_preexisting_connected(self, mock_status, fake_session):
        mock_status.return_value = ProfileState.CONNECTED
        self._lane(fake_session).execute()
        _assert_deal_state(fake_session, "alice", ProfileState.CONNECTED)

    @patch("linkedin.actions.connection_status.get_connection_status")
    def test_detects_already_pending(self, mock_status, fake_session):
        mock_status.return_value = ProfileState.PENDING
        self._lane(fake_session).execute()
        _assert_deal_state(fake_session, "alice", ProfileState.PENDING)

    @patch("linkedin.actions.connection_status.get_connection_status")
    def test_handles_rate_limit(self, mock_status, fake_session):
        mock_status.side_effect = ReachedConnectionLimit("weekly limit")
        self._lane(fake_session).execute()
        assert ActionLog.ActionType.CONNECT in fake_session.linkedin_profile._exhausted

    @patch("linkedin.actions.connect.send_connection_request")
    @patch("linkedin.actions.connection_status.get_connection_status")
    def test_handles_skip_profile(self, mock_status, mock_send, fake_session):
        mock_status.return_value = ProfileState.NEW
        mock_send.side_effect = SkipProfile("bad profile")
        self._lane(fake_session).execute()
        _assert_deal_state(fake_session, "alice", ProfileState.FAILED)


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
    def test_transitions_to_connected(self, mock_status, fake_session):
        mock_status.return_value = ProfileState.CONNECTED
        self._setup(fake_session).execute()
        _assert_deal_state(fake_session, "alice", ProfileState.CONNECTED)

    @patch("linkedin.actions.connection_status.get_connection_status")
    def test_stays_pending(self, mock_status, fake_session):
        mock_status.return_value = ProfileState.PENDING
        self._setup(fake_session).execute()
        _assert_deal_state(fake_session, "alice", ProfileState.PENDING)

    @patch("linkedin.actions.connection_status.get_connection_status")
    def test_doubles_backoff(self, mock_status, fake_session):
        import json
        mock_status.return_value = ProfileState.PENDING
        self._setup(fake_session).execute()
        from crm.models import Deal
        from linkedin.db.crm_profiles import public_id_to_url
        deal = Deal.objects.get(lead__website=public_id_to_url("alice"))
        assert json.loads(deal.next_step)["backoff_hours"] == 144

    @patch("linkedin.actions.connection_status.get_connection_status")
    def test_doubles_existing_backoff(self, mock_status, fake_session):
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
        CheckPendingLane(fake_session, recheck_after_hours=72).execute()
        deal = Deal.objects.get(lead__website=public_id_to_url("bob"))
        assert json.loads(deal.next_step)["backoff_hours"] == 20

    @patch("linkedin.actions.connection_status.get_connection_status")
    def test_noop_when_empty(self, mock_status, fake_session):
        CheckPendingLane(fake_session, recheck_after_hours=72).execute()
        mock_status.assert_not_called()


# ── FollowUpLane tests ─────────────────────────────────────


@pytest.mark.django_db
class TestFollowUpLane:
    def test_can_execute_with_connected(self, fake_session):
        _make_connected(fake_session)
        assert FollowUpLane(fake_session).can_execute() is True

    def test_cannot_execute_rate_limited(self, fake_session):
        _make_connected(fake_session)
        fake_session.linkedin_profile.follow_up_daily_limit = 0
        fake_session.linkedin_profile.save(update_fields=["follow_up_daily_limit"])
        assert FollowUpLane(fake_session).can_execute() is False

    def test_cannot_execute_empty(self, fake_session):
        assert FollowUpLane(fake_session).can_execute() is False

    @patch("linkedin.actions.message.send_follow_up_message")
    def test_sends_message_and_completes(self, mock_send, fake_session):
        mock_send.return_value = "Hello Alice!"
        _make_connected(fake_session)
        FollowUpLane(fake_session).execute()
        _assert_deal_state(fake_session, "alice", ProfileState.COMPLETED)
        assert ActionLog.objects.filter(action_type=ActionLog.ActionType.FOLLOW_UP).count() == 1

    @patch("linkedin.actions.message.send_follow_up_message")
    def test_saves_chat_message(self, mock_send, fake_session):
        from chat.models import ChatMessage
        from django.contrib.contenttypes.models import ContentType
        from crm.models import Lead

        mock_send.return_value = "Hello Alice!"
        _make_connected(fake_session)
        FollowUpLane(fake_session).execute()

        lead = Lead.objects.get(website="https://www.linkedin.com/in/alice/")
        ct = ContentType.objects.get_for_model(lead)
        msg = ChatMessage.objects.get(content_type=ct, object_id=lead.pk)
        assert msg.content == "Hello Alice!"

    @patch("linkedin.actions.message.send_follow_up_message")
    def test_skipped_message_stays_connected(self, mock_send, fake_session):
        mock_send.return_value = None
        _make_connected(fake_session)
        FollowUpLane(fake_session).execute()
        _assert_deal_state(fake_session, "alice", ProfileState.CONNECTED)

    @patch("linkedin.actions.message.send_follow_up_message")
    def test_noop_when_empty(self, mock_send, fake_session):
        FollowUpLane(fake_session).execute()
        mock_send.assert_not_called()
