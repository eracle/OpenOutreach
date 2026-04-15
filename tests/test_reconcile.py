# tests/test_reconcile.py
import pytest
from django.utils import timezone

from linkedin.db.deals import set_profile_state
from linkedin.db.leads import create_enriched_lead, promote_lead_to_deal
from linkedin.models import Task
from linkedin.enums import ProfileState
from linkedin.tasks.scheduler import reconcile


SAMPLE_PROFILE = {
    "first_name": "Alice",
    "last_name": "Smith",
    "headline": "Engineer",
    "positions": [{"company_name": "Acme"}],
}


def _make_pending(session, public_id="alice"):
    url = f"https://www.linkedin.com/in/{public_id}/"
    create_enriched_lead(session, url, SAMPLE_PROFILE)
    promote_lead_to_deal(session, public_id)
    set_profile_state(session, public_id, ProfileState.PENDING.value)
    # set_profile_state's scheduler hook auto-enqueues — wipe so heal tests
    # exercise reconcile from a clean queue.
    Task.objects.all().delete()


def _make_connected(session, public_id="alice"):
    url = f"https://www.linkedin.com/in/{public_id}/"
    create_enriched_lead(session, url, SAMPLE_PROFILE)
    promote_lead_to_deal(session, public_id)
    set_profile_state(session, public_id, ProfileState.CONNECTED.value)
    Task.objects.all().delete()


@pytest.mark.django_db
class TestReconcile:
    @pytest.fixture(autouse=True)
    def _db(self, db):
        pass

    def test_recovers_stale_running_tasks(self, fake_session):
        Task.objects.create(
            task_type=Task.TaskType.CONNECT,
            status=Task.Status.RUNNING,
            scheduled_at=timezone.now(),
            payload={"campaign_id": fake_session.campaign.pk},
        )
        reconcile(fake_session)
        assert Task.objects.filter(status=Task.Status.RUNNING).count() == 0
        assert Task.objects.filter(
            task_type=Task.TaskType.CONNECT,
            status=Task.Status.PENDING,
        ).exists()

    def test_seeds_connect_per_campaign(self, fake_session):
        reconcile(fake_session)
        assert Task.objects.filter(
            task_type=Task.TaskType.CONNECT,
            status=Task.Status.PENDING,
            payload__campaign_id=fake_session.campaign.pk,
        ).count() == 1

    def test_creates_check_pending_for_pending_profiles(self, fake_session):
        _make_pending(fake_session, "alice")
        reconcile(fake_session)
        assert Task.objects.filter(
            task_type=Task.TaskType.CHECK_PENDING,
            status=Task.Status.PENDING,
            payload__public_id="alice",
        ).exists()

    def test_uses_deal_backoff_for_check_pending(self, fake_session):
        _make_pending(fake_session, "alice")
        from crm.models import Deal
        from linkedin.url_utils import public_id_to_url
        Deal.objects.filter(
            lead__linkedin_url=public_id_to_url("alice"),
        ).update(backoff_hours=96)

        reconcile(fake_session)
        task = Task.objects.get(
            task_type=Task.TaskType.CHECK_PENDING,
            payload__public_id="alice",
        )
        assert task.payload["backoff_hours"] == 96

    def test_creates_follow_up_for_connected_profiles(self, fake_session):
        _make_connected(fake_session, "alice")
        reconcile(fake_session)
        assert Task.objects.filter(
            task_type=Task.TaskType.FOLLOW_UP,
            status=Task.Status.PENDING,
            payload__public_id="alice",
        ).exists()

    def test_no_duplicates_on_second_heal(self, fake_session):
        _make_pending(fake_session, "alice")
        _make_connected(fake_session, "bob")
        reconcile(fake_session)
        count_before = Task.objects.filter(status=Task.Status.PENDING).count()
        reconcile(fake_session)
        count_after = Task.objects.filter(status=Task.Status.PENDING).count()
        assert count_before == count_after

    def test_does_not_create_for_completed_tasks(self, fake_session):
        """Already-completed tasks should not block reconcile from creating new ones."""
        _make_pending(fake_session, "alice")
        # Create a completed check_pending task for alice
        Task.objects.create(
            task_type=Task.TaskType.CHECK_PENDING,
            status=Task.Status.COMPLETED,
            scheduled_at=timezone.now(),
            payload={"campaign_id": fake_session.campaign.pk, "public_id": "alice", "backoff_hours": 24},
        )
        reconcile(fake_session)
        # Should still create a new pending task
        assert Task.objects.filter(
            task_type=Task.TaskType.CHECK_PENDING,
            status=Task.Status.PENDING,
            payload__public_id="alice",
        ).exists()

    def test_recreates_task_after_handler_crash(self, fake_session):
        """The retry mechanism: a FAILED task on an active deal leaves the deal
        without a pending task. Reconcile must re-create one on the next idle
        cycle so the pipeline doesn't stall."""
        _make_pending(fake_session, "alice")
        # Simulate a crashed handler: a FAILED check_pending task with no
        # pending successor.
        Task.objects.create(
            task_type=Task.TaskType.CHECK_PENDING,
            status=Task.Status.FAILED,
            scheduled_at=timezone.now(),
            payload={"campaign_id": fake_session.campaign.pk, "public_id": "alice", "backoff_hours": 24},
        )
        assert not Task.objects.filter(
            task_type=Task.TaskType.CHECK_PENDING,
            status=Task.Status.PENDING,
            payload__public_id="alice",
        ).exists()

        reconcile(fake_session)

        assert Task.objects.filter(
            task_type=Task.TaskType.CHECK_PENDING,
            status=Task.Status.PENDING,
            payload__public_id="alice",
        ).exists()
