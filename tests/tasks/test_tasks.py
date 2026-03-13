# tests/tasks/test_tasks.py
import pytest
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.utils import timezone

from linkedin.db.deals import set_profile_state
from linkedin.db.leads import create_enriched_lead, promote_lead_to_contact
from linkedin.models import ActionLog, Task
from linkedin.ml.qualifier import BayesianQualifier
from linkedin.enums import ProfileState
from linkedin.exceptions import SkipProfile, ReachedConnectionLimit
from linkedin.tasks.connect import ConnectStrategy, handle_connect
from linkedin.tasks.check_pending import handle_check_pending
from linkedin.tasks.follow_up import handle_follow_up


SAMPLE_PROFILE = {
    "first_name": "Alice",
    "last_name": "Smith",
    "headline": "Engineer",
    "positions": [{"company_name": "Acme"}],
}


def _mock_strategy(candidate, qualifier=None):
    """Build a ConnectStrategy that returns a fixed candidate."""
    return ConnectStrategy(
        find_candidate=lambda s: candidate,
        pre_connect=None,
        delay=10,
        action_fraction=1.0,
        qualifier=qualifier or MagicMock(explain=lambda *a, **kw: ""),
    )


def _assert_deal_state(session, public_id, expected_state: ProfileState):
    from crm.models import Deal
    deal = Deal.objects.get(
        lead__website=f"https://www.linkedin.com/in/{public_id}/",
        owner=session.django_user,
    )
    assert deal.stage.name == expected_state.value


def _make_qualified(session, public_id="alice"):
    url = f"https://www.linkedin.com/in/{public_id}/"
    create_enriched_lead(session, url, SAMPLE_PROFILE)
    promote_lead_to_contact(session, public_id)


def _make_pending(session, public_id="alice"):
    _make_qualified(session, public_id)
    set_profile_state(session, public_id, ProfileState.PENDING.value)


def _make_connected(session, public_id="alice"):
    _make_qualified(session, public_id)
    set_profile_state(session, public_id, ProfileState.CONNECTED.value)


def _make_old_deal(session, days):
    from crm.models import Deal
    deal = Deal.objects.filter(owner=session.django_user).first()
    Deal.objects.filter(pk=deal.pk).update(
        update_date=timezone.now() - timedelta(days=days)
    )


def _make_task(task_type, payload, **kwargs):
    """Create a task and mark it RUNNING (matching daemon behavior)."""
    return Task.objects.create(
        task_type=task_type,
        status=Task.Status.RUNNING,
        scheduled_at=kwargs.pop("scheduled_at", timezone.now()),
        started_at=timezone.now(),
        payload=payload,
        **kwargs,
    )


def _build_context(fake_session):
    """Build qualifiers dict for task handlers."""
    qualifier = BayesianQualifier(seed=42)
    qualifier.rank_profiles = lambda profiles, **kw: profiles
    return {fake_session.campaign.pk: qualifier}


# ── handle_connect tests ────────────────────────────────────────


@pytest.mark.django_db
class TestHandleConnect:
    @pytest.fixture(autouse=True)
    def _db(self, embeddings_db):
        pass

    def _candidate(self):
        return {"public_identifier": "alice", "url": "https://www.linkedin.com/in/alice/", "profile": SAMPLE_PROFILE}

    @patch("linkedin.tasks.connect.strategy_for")
    @patch("linkedin.actions.connect.send_connection_request")
    @patch("linkedin.actions.status.get_connection_status")
    def test_sends_connection_and_records(self, mock_status, mock_send, mock_strategy, fake_session):
        _make_qualified(fake_session)
        mock_strategy.return_value = _mock_strategy(self._candidate())
        mock_status.return_value = ProfileState.QUALIFIED
        mock_send.return_value = ProfileState.PENDING

        task = _make_task(Task.TaskType.CONNECT, {"campaign_id": fake_session.campaign.pk})
        qualifiers = _build_context(fake_session)
        handle_connect(task, fake_session, qualifiers)

        _assert_deal_state(fake_session, "alice", ProfileState.PENDING)
        assert ActionLog.objects.filter(action_type=ActionLog.ActionType.CONNECT).count() == 1

    @patch("linkedin.tasks.connect.strategy_for")
    @patch("linkedin.actions.connect.send_connection_request")
    @patch("linkedin.actions.status.get_connection_status")
    def test_enqueues_check_pending_after_connect(self, mock_status, mock_send, mock_strategy, fake_session):
        _make_qualified(fake_session)
        mock_strategy.return_value = _mock_strategy(self._candidate())
        mock_status.return_value = ProfileState.QUALIFIED
        mock_send.return_value = ProfileState.PENDING

        task = _make_task(Task.TaskType.CONNECT, {"campaign_id": fake_session.campaign.pk})
        qualifiers = _build_context(fake_session)
        handle_connect(task, fake_session, qualifiers)

        assert Task.objects.filter(
            task_type=Task.TaskType.CHECK_PENDING,
            status=Task.Status.PENDING,
            payload__public_id="alice",
        ).exists()

    @patch("linkedin.tasks.connect.strategy_for")
    @patch("linkedin.actions.status.get_connection_status")
    def test_marks_preexisting_connected(self, mock_status, mock_strategy, fake_session):
        _make_qualified(fake_session)
        mock_strategy.return_value = _mock_strategy(self._candidate())
        mock_status.return_value = ProfileState.CONNECTED

        task = _make_task(Task.TaskType.CONNECT, {"campaign_id": fake_session.campaign.pk})
        qualifiers = _build_context(fake_session)
        handle_connect(task, fake_session, qualifiers)

        _assert_deal_state(fake_session, "alice", ProfileState.CONNECTED)
        # Should enqueue follow_up for already-connected profile
        assert Task.objects.filter(
            task_type=Task.TaskType.FOLLOW_UP,
            status=Task.Status.PENDING,
            payload__public_id="alice",
        ).exists()

    @patch("linkedin.tasks.connect.strategy_for")
    @patch("linkedin.actions.status.get_connection_status")
    def test_handles_rate_limit(self, mock_status, mock_strategy, fake_session):
        _make_qualified(fake_session)
        mock_strategy.return_value = _mock_strategy(self._candidate())
        mock_status.side_effect = ReachedConnectionLimit("weekly limit")

        task = _make_task(Task.TaskType.CONNECT, {"campaign_id": fake_session.campaign.pk})
        qualifiers = _build_context(fake_session)
        handle_connect(task, fake_session, qualifiers)

        assert ActionLog.ActionType.CONNECT in fake_session.linkedin_profile._exhausted

    @patch("linkedin.tasks.connect.strategy_for")
    @patch("linkedin.actions.connect.send_connection_request")
    @patch("linkedin.actions.status.get_connection_status")
    def test_handles_skip_profile(self, mock_status, mock_send, mock_strategy, fake_session):
        _make_qualified(fake_session)
        mock_strategy.return_value = _mock_strategy(self._candidate())
        mock_status.return_value = ProfileState.QUALIFIED
        mock_send.side_effect = SkipProfile("bad profile")

        task = _make_task(Task.TaskType.CONNECT, {"campaign_id": fake_session.campaign.pk})
        qualifiers = _build_context(fake_session)
        handle_connect(task, fake_session, qualifiers)

        _assert_deal_state(fake_session, "alice", ProfileState.FAILED)

    @patch("linkedin.tasks.connect.strategy_for")
    def test_reschedules_when_no_candidate(self, mock_strategy, fake_session):
        mock_strategy.return_value = _mock_strategy(None)

        task = _make_task(Task.TaskType.CONNECT, {"campaign_id": fake_session.campaign.pk})
        qualifiers = _build_context(fake_session)
        handle_connect(task, fake_session, qualifiers)

        # Should enqueue another connect with longer delay
        next_task = Task.objects.filter(
            task_type=Task.TaskType.CONNECT,
            status=Task.Status.PENDING,
            payload__campaign_id=fake_session.campaign.pk,
        ).exclude(pk=task.pk).first()
        assert next_task is not None

    @patch("linkedin.tasks.connect.strategy_for")
    @patch("linkedin.actions.connect.send_connection_request")
    @patch("linkedin.actions.status.get_connection_status")
    def test_self_reschedules_connect(self, mock_status, mock_send, mock_strategy, fake_session):
        _make_qualified(fake_session)
        mock_strategy.return_value = _mock_strategy(self._candidate())
        mock_status.return_value = ProfileState.QUALIFIED
        mock_send.return_value = ProfileState.PENDING

        task = _make_task(Task.TaskType.CONNECT, {"campaign_id": fake_session.campaign.pk})
        qualifiers = _build_context(fake_session)
        handle_connect(task, fake_session, qualifiers)

        # Should have enqueued next connect task
        next_connect = Task.objects.filter(
            task_type=Task.TaskType.CONNECT,
            status=Task.Status.PENDING,
            payload__campaign_id=fake_session.campaign.pk,
        ).exclude(pk=task.pk).first()
        assert next_connect is not None


# ── handle_check_pending tests ──────────────────────────────────────


@pytest.mark.django_db
class TestHandleCheckPending:
    @pytest.fixture(autouse=True)
    def _db(self, embeddings_db):
        pass

    @patch("linkedin.actions.status.get_connection_status")
    def test_transitions_to_connected(self, mock_status, fake_session):
        mock_status.return_value = ProfileState.CONNECTED
        _make_pending(fake_session)

        task = _make_task(
            Task.TaskType.CHECK_PENDING,
            {"campaign_id": fake_session.campaign.pk, "public_id": "alice", "backoff_hours": 24},
        )
        qualifiers = _build_context(fake_session)
        handle_check_pending(task, fake_session, qualifiers)

        _assert_deal_state(fake_session, "alice", ProfileState.CONNECTED)
        # Should enqueue follow_up
        assert Task.objects.filter(
            task_type=Task.TaskType.FOLLOW_UP,
            payload__public_id="alice",
        ).exists()

    @patch("linkedin.actions.status.get_connection_status")
    def test_stays_pending_and_doubles_backoff(self, mock_status, fake_session):
        import json
        mock_status.return_value = ProfileState.PENDING
        _make_pending(fake_session)

        task = _make_task(
            Task.TaskType.CHECK_PENDING,
            {"campaign_id": fake_session.campaign.pk, "public_id": "alice", "backoff_hours": 72},
        )
        qualifiers = _build_context(fake_session)
        handle_check_pending(task, fake_session, qualifiers)

        _assert_deal_state(fake_session, "alice", ProfileState.PENDING)

        # Deal should have doubled backoff
        from crm.models import Deal
        from linkedin.db.urls import public_id_to_url
        deal = Deal.objects.get(lead__website=public_id_to_url("alice"))
        assert json.loads(deal.next_step)["backoff_hours"] == 144

        # Should have re-enqueued check_pending with new backoff
        next_task = Task.objects.filter(
            task_type=Task.TaskType.CHECK_PENDING,
            status=Task.Status.PENDING,
            payload__public_id="alice",
        ).exclude(pk=task.pk).first()
        assert next_task is not None
        assert next_task.payload["backoff_hours"] == 144

    @patch("linkedin.actions.status.get_connection_status")
    def test_noop_when_deal_missing(self, mock_status, fake_session):
        task = _make_task(
            Task.TaskType.CHECK_PENDING,
            {"campaign_id": fake_session.campaign.pk, "public_id": "nonexistent", "backoff_hours": 24},
        )
        qualifiers = _build_context(fake_session)
        handle_check_pending(task, fake_session, qualifiers)
        mock_status.assert_not_called()


# ── handle_follow_up tests ─────────────────────────────────────


@pytest.mark.django_db
class TestHandleFollowUp:
    @patch("linkedin.agents.follow_up.run_follow_up_agent")
    def test_agent_sends_message_and_records_action(self, mock_agent, fake_session):
        mock_agent.return_value = {
            "messages": [],
            "actions": [
                {"tool": "send_message", "args": {"message": "Hello Alice!"}},
                {"tool": "schedule_follow_up", "args": {"hours": 72}},
            ],
        }
        _make_connected(fake_session)

        task = _make_task(
            Task.TaskType.FOLLOW_UP,
            {"campaign_id": fake_session.campaign.pk, "public_id": "alice"},
        )
        qualifiers = _build_context(fake_session)
        handle_follow_up(task, fake_session, qualifiers)

        mock_agent.assert_called_once()
        assert ActionLog.objects.filter(action_type=ActionLog.ActionType.FOLLOW_UP).count() == 1

    @patch("linkedin.agents.follow_up.run_follow_up_agent")
    def test_agent_no_message_no_action_recorded(self, mock_agent, fake_session):
        mock_agent.return_value = {
            "messages": [],
            "actions": [
                {"tool": "mark_completed", "args": {"reason": "Lead went cold"}},
            ],
        }
        _make_connected(fake_session)

        task = _make_task(
            Task.TaskType.FOLLOW_UP,
            {"campaign_id": fake_session.campaign.pk, "public_id": "alice"},
        )
        qualifiers = _build_context(fake_session)
        handle_follow_up(task, fake_session, qualifiers)

        assert ActionLog.objects.filter(action_type=ActionLog.ActionType.FOLLOW_UP).count() == 0

    @patch("linkedin.agents.follow_up.run_follow_up_agent")
    def test_noop_when_deal_missing(self, mock_agent, fake_session):
        task = _make_task(
            Task.TaskType.FOLLOW_UP,
            {"campaign_id": fake_session.campaign.pk, "public_id": "nonexistent"},
        )
        qualifiers = _build_context(fake_session)
        handle_follow_up(task, fake_session, qualifiers)
        mock_agent.assert_not_called()

    def test_reschedules_on_rate_limit(self, fake_session):
        _make_connected(fake_session)
        fake_session.linkedin_profile.follow_up_daily_limit = 0
        fake_session.linkedin_profile.save(update_fields=["follow_up_daily_limit"])

        task = _make_task(
            Task.TaskType.FOLLOW_UP,
            {"campaign_id": fake_session.campaign.pk, "public_id": "alice"},
        )
        qualifiers = _build_context(fake_session)
        handle_follow_up(task, fake_session, qualifiers)

        # Should have re-enqueued with delay
        next_task = Task.objects.filter(
            task_type=Task.TaskType.FOLLOW_UP,
            status=Task.Status.PENDING,
            payload__public_id="alice",
        ).exclude(pk=task.pk).first()
        assert next_task is not None
