import pytest
from django.db import IntegrityError

from openoutreach.core.models import ActionLog


@pytest.mark.django_db
def test_action_log_duplicate_non_blank_idempotency_key_raises_integrity_error():
    ActionLog.objects.create(
        action_type="email.send_next",
        target_type="campaign",
        target_id="1",
        payload_hash="hash-a",
        idempotency_key="email-1",
        status=ActionLog.Status.PLANNED,
    )

    with pytest.raises(IntegrityError):
        ActionLog.objects.create(
            action_type="email.send_next",
            target_type="campaign",
            target_id="2",
            payload_hash="hash-b",
            idempotency_key="email-1",
            status=ActionLog.Status.PLANNED,
        )


@pytest.mark.django_db
def test_action_log_allows_blank_idempotency_key_duplicates():
    first = ActionLog.objects.create(
        action_type="email.send_next",
        target_type="campaign",
        target_id="1",
        payload_hash="hash-a",
        idempotency_key="",
        status=ActionLog.Status.PLANNED,
    )
    second = ActionLog.objects.create(
        action_type="email.send_next",
        target_type="campaign",
        target_id="2",
        payload_hash="hash-b",
        idempotency_key="",
        status=ActionLog.Status.PLANNED,
    )

    assert first.pk != second.pk


@pytest.mark.django_db
def test_action_log_records_result_and_error():
    action = ActionLog.objects.create(
        action_type="task.run_next",
        target_type="task",
        target_id="7",
        payload_hash="hash-b",
        idempotency_key="task-7",
        status=ActionLog.Status.PLANNED,
        error_type="stale_error",
        error_message="stale message",
    )

    action.mark_succeeded({"task_id": 7})
    action.refresh_from_db()
    assert action.status == ActionLog.Status.SUCCEEDED
    assert action.result == {"task_id": 7}
    assert action.error_type == ""
    assert action.error_message == ""

    action.mark_failed("no_eligible_email", "No eligible READY_TO_EMAIL deal exists.")
    action.refresh_from_db()
    assert action.status == ActionLog.Status.FAILED
    assert action.result is None
    assert action.error_type == "no_eligible_email"
    assert action.error_message == "No eligible READY_TO_EMAIL deal exists."
