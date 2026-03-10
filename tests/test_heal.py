# tests/test_heal.py
import json
import pytest
from django.utils import timezone

from linkedin.daemon import heal_tasks
from linkedin.db.crm_profiles import (
    create_enriched_lead,
    promote_lead_to_contact,
    set_profile_state,
)
from linkedin.models import Task
from linkedin.navigation.enums import ProfileState


SAMPLE_PROFILE = {
    "first_name": "Alice",
    "last_name": "Smith",
    "headline": "Engineer",
    "positions": [{"company_name": "Acme"}],
}


def _make_pending(session, public_id="alice"):
    url = f"https://www.linkedin.com/in/{public_id}/"
    create_enriched_lead(session, url, SAMPLE_PROFILE)
    promote_lead_to_contact(session, public_id)
    set_profile_state(session, public_id, ProfileState.PENDING.value)


def _make_connected(session, public_id="alice"):
    url = f"https://www.linkedin.com/in/{public_id}/"
    create_enriched_lead(session, url, SAMPLE_PROFILE)
    promote_lead_to_contact(session, public_id)
    set_profile_state(session, public_id, ProfileState.CONNECTED.value)


@pytest.mark.django_db
class TestHealTasks:
    @pytest.fixture(autouse=True)
    def _db(self, embeddings_db):
        pass

    def test_recovers_stale_running_tasks(self, fake_session):
        Task.objects.create(
            task_type=Task.TaskType.CONNECT,
            status=Task.Status.RUNNING,
            scheduled_at=timezone.now(),
            payload={"campaign_id": fake_session.campaign.pk},
        )
        heal_tasks(fake_session)
        assert Task.objects.filter(status=Task.Status.RUNNING).count() == 0
        assert Task.objects.filter(
            task_type=Task.TaskType.CONNECT,
            status=Task.Status.PENDING,
        ).exists()

    def test_seeds_connect_per_campaign(self, fake_session):
        heal_tasks(fake_session)
        assert Task.objects.filter(
            task_type=Task.TaskType.CONNECT,
            status=Task.Status.PENDING,
            payload__campaign_id=fake_session.campaign.pk,
        ).count() == 1

    def test_creates_check_pending_for_pending_profiles(self, fake_session):
        _make_pending(fake_session, "alice")
        heal_tasks(fake_session)
        assert Task.objects.filter(
            task_type=Task.TaskType.CHECK_PENDING,
            status=Task.Status.PENDING,
            payload__public_id="alice",
        ).exists()

    def test_uses_deal_backoff_for_check_pending(self, fake_session):
        _make_pending(fake_session, "alice")
        from crm.models import Deal
        from linkedin.db.crm_profiles import public_id_to_url
        Deal.objects.filter(
            lead__website=public_id_to_url("alice"),
        ).update(next_step=json.dumps({"backoff_hours": 96}))

        heal_tasks(fake_session)
        task = Task.objects.get(
            task_type=Task.TaskType.CHECK_PENDING,
            payload__public_id="alice",
        )
        assert task.payload["backoff_hours"] == 96

    def test_creates_follow_up_for_connected_profiles(self, fake_session):
        _make_connected(fake_session, "alice")
        heal_tasks(fake_session)
        assert Task.objects.filter(
            task_type=Task.TaskType.FOLLOW_UP,
            status=Task.Status.PENDING,
            payload__public_id="alice",
        ).exists()

    def test_no_duplicates_on_second_heal(self, fake_session):
        _make_pending(fake_session, "alice")
        _make_connected(fake_session, "bob")
        heal_tasks(fake_session)
        count_before = Task.objects.filter(status=Task.Status.PENDING).count()
        heal_tasks(fake_session)
        count_after = Task.objects.filter(status=Task.Status.PENDING).count()
        assert count_before == count_after

    def test_does_not_create_for_completed_tasks(self, fake_session):
        """Already-completed tasks should not block healing from creating new ones."""
        _make_pending(fake_session, "alice")
        # Create a completed check_pending task for alice
        Task.objects.create(
            task_type=Task.TaskType.CHECK_PENDING,
            status=Task.Status.COMPLETED,
            scheduled_at=timezone.now(),
            payload={"campaign_id": fake_session.campaign.pk, "public_id": "alice", "backoff_hours": 24},
        )
        heal_tasks(fake_session)
        # Should still create a new pending task
        assert Task.objects.filter(
            task_type=Task.TaskType.CHECK_PENDING,
            status=Task.Status.PENDING,
            payload__public_id="alice",
        ).exists()
