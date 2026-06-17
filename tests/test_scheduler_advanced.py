"""Tests for the per-type Poisson planner in ``linkedin/tasks/scheduler.py``."""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from openoutreach.core.db.deals import set_profile_state
from openoutreach.linkedin.db.leads import create_enriched_lead, promote_lead_to_deal
from linkedin_cli.enums import ProfileState
from openoutreach.core.models import Task
from openoutreach.linkedin.models import ActionLog
from openoutreach.core import scheduler


SAMPLE_PROFILE = {
    "first_name": "Alice",
    "last_name": "Smith",
    "headline": "Engineer",
    "positions": [{"company_name": "Acme"}],
}


# ── working_seconds_in_window ─────────────────────────────────────────


class TestWorkingSecondsInWindow:
    @patch("openoutreach.core.scheduler.ENABLE_ACTIVE_HOURS", False)
    def test_disabled_returns_full_horizon(self):
        now = datetime(2026, 5, 10, 12, tzinfo=ZoneInfo("UTC"))
        assert scheduler.working_seconds_in_window(now, now + timedelta(hours=24)) == 24 * 3600

    @patch("openoutreach.core.scheduler.ENABLE_ACTIVE_HOURS", True)
    @patch("openoutreach.core.scheduler.ACTIVE_START_HOUR", 9)
    @patch("openoutreach.core.scheduler.ACTIVE_END_HOUR", 19)
    @patch("openoutreach.core.scheduler.ACTIVE_TIMEZONE", "UTC")
    def test_18h_start_with_9_to_19_window(self):
        # Start at 18:00 → 1h today (18-19) + 9h tomorrow (9-18, since horizon ends at 18:00) = 10h
        now = datetime(2026, 5, 10, 18, tzinfo=ZoneInfo("UTC"))
        seconds = scheduler.working_seconds_in_window(now, now + timedelta(hours=24))
        assert seconds == pytest.approx(10 * 3600, abs=1)

    @patch("openoutreach.core.scheduler.ENABLE_ACTIVE_HOURS", True)
    @patch("openoutreach.core.scheduler.ACTIVE_START_HOUR", 9)
    @patch("openoutreach.core.scheduler.ACTIVE_END_HOUR", 19)
    @patch("openoutreach.core.scheduler.ACTIVE_TIMEZONE", "UTC")
    def test_inside_window_returns_remaining_plus_next_day(self):
        # Start at 09:00 → 10h today + 10h tomorrow up to 19:00 (= start + 24h hits 09:00) = 14h
        # Actually: now=09:00, end=09:00 next day. Today: 9-19 = 10h. Next day: 9-9 = 0h. Total = 10h.
        now = datetime(2026, 5, 10, 9, tzinfo=ZoneInfo("UTC"))
        seconds = scheduler.working_seconds_in_window(now, now + timedelta(hours=24))
        assert seconds == pytest.approx(10 * 3600, abs=1)


# ── poisson_slot_times ────────────────────────────────────────────────


class TestPoissonSlotTimes:
    @patch("openoutreach.core.scheduler.ENABLE_ACTIVE_HOURS", False)
    def test_zero_returns_empty(self):
        now = timezone.now()
        assert scheduler.poisson_slot_times(now, 0) == []

    @patch("openoutreach.core.scheduler.ENABLE_ACTIVE_HOURS", False)
    def test_strictly_increasing(self):
        random.seed(42)
        now = timezone.now()
        times = scheduler.poisson_slot_times(now, 20)
        assert all(t2 > t1 for t1, t2 in zip(times, times[1:]))

    @patch("openoutreach.core.scheduler.ENABLE_ACTIVE_HOURS", False)
    def test_all_inside_horizon(self):
        random.seed(42)
        now = timezone.now()
        end = now + timedelta(hours=24)
        for _ in range(20):
            times = scheduler.poisson_slot_times(now, 20)
            assert all(now <= t < end for t in times)

    @patch("openoutreach.core.scheduler.ENABLE_ACTIVE_HOURS", True)
    @patch("openoutreach.core.scheduler.ACTIVE_START_HOUR", 9)
    @patch("openoutreach.core.scheduler.ACTIVE_END_HOUR", 19)
    @patch("openoutreach.core.scheduler.ACTIVE_TIMEZONE", "UTC")
    def test_active_hours_constraint(self):
        random.seed(123)
        now = datetime(2026, 5, 10, 9, tzinfo=ZoneInfo("UTC"))
        for _ in range(10):
            times = scheduler.poisson_slot_times(now, 20)
            for t in times:
                local = t.astimezone(ZoneInfo("UTC"))
                assert 9 <= local.hour < 19, f"slot {local} outside [9,19)"

    @patch("openoutreach.core.scheduler.ENABLE_ACTIVE_HOURS", False)
    def test_mean_spacing_within_tolerance(self):
        """Over 1000 trials, mean wall-clock spacing matches T/(N+1) within ±20%."""
        random.seed(7)
        N = 20
        T = 24 * 3600.0
        expected = T / (N + 1)  # order-statistic spacing
        means = []
        now = timezone.now()
        for _ in range(1000):
            times = scheduler.poisson_slot_times(now, N)
            assert len(times) == N
            deltas = [(t2 - t1).total_seconds() for t1, t2 in zip(times, times[1:])]
            means.append(sum(deltas) / len(deltas))
        observed = sum(means) / len(means)
        assert abs(observed - expected) / expected < 0.20


# ── on_deal_state_entered ─────────────────────────────────────────────


@pytest.mark.django_db
class TestOnDealStateEntered:
    def _make_deal(self, fake_session, state=ProfileState.QUALIFIED):
        create_enriched_lead(fake_session, "https://www.linkedin.com/in/alice/", SAMPLE_PROFILE)
        promote_lead_to_deal(fake_session, "alice")
        from openoutreach.crm.models import Deal
        deal = Deal.objects.get(lead__public_identifier="alice", campaign=fake_session.campaign)
        deal.state = state
        deal.save(update_fields=["state"])
        Task.objects.all().delete()
        return deal

    def test_pending_stamps_next_check(self, fake_session):
        deal = self._make_deal(fake_session, ProfileState.PENDING)
        before = timezone.now()
        scheduler.on_deal_state_entered(deal)
        deal.refresh_from_db()
        assert deal.next_check_pending_at is not None
        assert deal.next_check_pending_at >= before
        # Default backoff is 24h
        assert deal.next_check_pending_at <= before + timedelta(hours=24, minutes=1)

    def test_pending_uses_deal_backoff(self, fake_session):
        deal = self._make_deal(fake_session, ProfileState.PENDING)
        deal.backoff_hours = 96
        deal.save(update_fields=["backoff_hours"])
        scheduler.on_deal_state_entered(deal)
        deal.refresh_from_db()
        expected = timezone.now() + timedelta(hours=96)
        assert abs((deal.next_check_pending_at - expected).total_seconds()) < 5

    def test_pending_creates_no_task(self, fake_session):
        deal = self._make_deal(fake_session, ProfileState.PENDING)
        scheduler.on_deal_state_entered(deal)
        assert Task.objects.count() == 0

    def test_connected_creates_no_task(self, fake_session):
        deal = self._make_deal(fake_session, ProfileState.CONNECTED)
        scheduler.on_deal_state_entered(deal)
        assert Task.objects.count() == 0

    def test_connected_does_not_stamp_next_check(self, fake_session):
        deal = self._make_deal(fake_session, ProfileState.CONNECTED)
        scheduler.on_deal_state_entered(deal)
        deal.refresh_from_db()
        assert deal.next_check_pending_at is None


# ── plan_*_window ─────────────────────────────────────────────────────


@pytest.mark.django_db
class TestPlanConnectWindow:
    @patch("openoutreach.core.scheduler.ENABLE_ACTIVE_HOURS", False)
    def test_creates_slots_for_remaining_daily(self, fake_session):
        fake_session.linkedin_profile.connect_daily_limit = 20
        fake_session.linkedin_profile.save(update_fields=["connect_daily_limit"])

        created = scheduler.plan_connect_window(fake_session, fake_session.campaign)
        assert created == 20
        tasks = Task.objects.filter(task_type=Task.TaskType.CONNECT)
        assert tasks.count() == 20
        for t in tasks:
            assert t.payload == {"campaign_id": fake_session.campaign.pk}

    @patch("openoutreach.core.scheduler.ENABLE_ACTIVE_HOURS", False)
    def test_noop_when_pending_exists(self, fake_session):
        Task.objects.create(
            task_type=Task.TaskType.CONNECT,
            status=Task.Status.PENDING,
            scheduled_at=timezone.now(),
            payload={"campaign_id": fake_session.campaign.pk},
        )
        created = scheduler.plan_connect_window(fake_session, fake_session.campaign)
        assert created == 0
        assert Task.objects.filter(task_type=Task.TaskType.CONNECT).count() == 1

    @patch("openoutreach.core.scheduler.ENABLE_ACTIVE_HOURS", False)
    def test_respects_today_executed(self, fake_session):
        fake_session.linkedin_profile.connect_daily_limit = 5
        fake_session.linkedin_profile.save(update_fields=["connect_daily_limit"])
        for _ in range(3):
            fake_session.linkedin_profile.record_action(
                ActionLog.ActionType.CONNECT, fake_session.campaign,
            )
        created = scheduler.plan_connect_window(fake_session, fake_session.campaign)
        assert created == 2  # 5 - 3

    @patch("openoutreach.core.scheduler.ENABLE_ACTIVE_HOURS", False)
    def test_leading_slot_fires_immediately(self, fake_session):
        fake_session.linkedin_profile.connect_daily_limit = 5
        fake_session.linkedin_profile.save(update_fields=["connect_daily_limit"])
        before = timezone.now()
        scheduler.plan_connect_window(fake_session, fake_session.campaign)
        earliest = (
            Task.objects.filter(task_type=Task.TaskType.CONNECT)
            .order_by("scheduled_at")
            .first()
        )
        assert earliest.scheduled_at >= before
        assert earliest.scheduled_at <= timezone.now()


@pytest.mark.django_db
class TestPlanFollowUpWindow:
    @patch("openoutreach.core.scheduler.ENABLE_ACTIVE_HOURS", False)
    def test_creates_slots_for_remaining_daily(self, fake_session):
        fake_session.linkedin_profile.follow_up_daily_limit = 25
        fake_session.linkedin_profile.save(update_fields=["follow_up_daily_limit"])

        created = scheduler.plan_follow_up_window(fake_session, fake_session.campaign)
        assert created == 25
        tasks = Task.objects.filter(task_type=Task.TaskType.FOLLOW_UP)
        assert tasks.count() == 25
        for t in tasks:
            assert t.payload == {"campaign_id": fake_session.campaign.pk}

    @patch("openoutreach.core.scheduler.ENABLE_ACTIVE_HOURS", False)
    def test_noop_when_pending_exists(self, fake_session):
        Task.objects.create(
            task_type=Task.TaskType.FOLLOW_UP,
            status=Task.Status.PENDING,
            scheduled_at=timezone.now(),
            payload={"campaign_id": fake_session.campaign.pk},
        )
        created = scheduler.plan_follow_up_window(fake_session, fake_session.campaign)
        assert created == 0
        assert Task.objects.filter(task_type=Task.TaskType.FOLLOW_UP).count() == 1


@pytest.mark.django_db
class TestPlanCheckPendingWindow:
    def _make_due_pending(self, fake_session, public_id, due_offset_hours=-1):
        url = f"https://www.linkedin.com/in/{public_id}/"
        create_enriched_lead(fake_session, url, SAMPLE_PROFILE)
        promote_lead_to_deal(fake_session, public_id)
        from openoutreach.crm.models import Deal
        deal = Deal.objects.get(lead__public_identifier=public_id, campaign=fake_session.campaign)
        deal.state = ProfileState.PENDING
        deal.next_check_pending_at = timezone.now() + timedelta(hours=due_offset_hours)
        deal.save(update_fields=["state", "next_check_pending_at"])
        return deal

    @patch("openoutreach.core.scheduler.ENABLE_ACTIVE_HOURS", False)
    def test_counts_due_deals(self, fake_session):
        for i in range(7):
            self._make_due_pending(fake_session, f"due{i}")
        Task.objects.all().delete()
        created = scheduler.plan_check_pending_window(fake_session, fake_session.campaign)
        assert created == 7
        for t in Task.objects.filter(task_type=Task.TaskType.CHECK_PENDING):
            assert t.payload == {"campaign_id": fake_session.campaign.pk}

    @patch("openoutreach.core.scheduler.ENABLE_ACTIVE_HOURS", False)
    @patch("openoutreach.core.scheduler.CHECK_PENDING_DAILY_CAP", 4)
    def test_respects_daily_cap(self, fake_session):
        for i in range(10):
            self._make_due_pending(fake_session, f"due{i}")
        Task.objects.all().delete()
        created = scheduler.plan_check_pending_window(fake_session, fake_session.campaign)
        assert created == 4

    @patch("openoutreach.core.scheduler.ENABLE_ACTIVE_HOURS", False)
    def test_only_counts_deals_due_now(self, fake_session):
        # Must match the handler's own due filter — counting deals due
        # *later* would plan an immediate slot the handler finds nothing
        # to do for, spinning reconcile in a tight no-op loop.
        self._make_due_pending(fake_session, "due_now", due_offset_hours=-1)
        self._make_due_pending(fake_session, "due_soon", due_offset_hours=12)
        self._make_due_pending(fake_session, "due_later", due_offset_hours=48)
        Task.objects.all().delete()
        created = scheduler.plan_check_pending_window(fake_session, fake_session.campaign)
        assert created == 1  # only due_now; due_soon/due_later are not yet due

    @patch("openoutreach.core.scheduler.ENABLE_ACTIVE_HOURS", False)
    def test_noop_when_pending_exists(self, fake_session):
        self._make_due_pending(fake_session, "alice")
        Task.objects.create(
            task_type=Task.TaskType.CHECK_PENDING,
            status=Task.Status.PENDING,
            scheduled_at=timezone.now(),
            payload={"campaign_id": fake_session.campaign.pk},
        )
        created = scheduler.plan_check_pending_window(fake_session, fake_session.campaign)
        assert created == 0


@pytest.mark.django_db
class TestEmailFirstClaimOrder:
    """``claim_next`` must hand back a ready EMAIL task before any other ready
    task, so a fresh daemon (or any idle cycle) sends queued email first."""

    def _make(self, task_type, scheduled_at, campaign_id=1):
        return Task.objects.create(
            task_type=task_type,
            status=Task.Status.PENDING,
            scheduled_at=scheduled_at,
            payload={"campaign_id": campaign_id},
        )

    def test_email_preempts_older_linkedin_task(self):
        now = timezone.now()
        # A connect task scheduled earlier than the email still loses to it.
        self._make(Task.TaskType.CONNECT, now - timedelta(hours=1))
        self._make(Task.TaskType.EMAIL, now)
        assert Task.objects.claim_next().task_type == Task.TaskType.EMAIL

    def test_falls_back_to_scheduled_at_without_email(self):
        now = timezone.now()
        self._make(Task.TaskType.FOLLOW_UP, now)
        older = self._make(Task.TaskType.CONNECT, now - timedelta(hours=1))
        assert Task.objects.claim_next().pk == older.pk

    def test_future_email_is_not_claimed_early(self):
        now = timezone.now()
        # Email ranks first, but a not-yet-due email must not be claimed, and
        # must not shadow a ready LinkedIn task.
        self._make(Task.TaskType.EMAIL, now + timedelta(hours=1))
        ready = self._make(Task.TaskType.CONNECT, now)
        assert Task.objects.claim_next().pk == ready.pk
