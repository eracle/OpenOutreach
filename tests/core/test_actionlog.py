import pytest

from openoutreach.core.models import ActionLog


@pytest.mark.django_db
def test_action_log_idempotency_is_unique_per_action_type():
    first = ActionLog.objects.create(
        action_type="email.send_next",
        target_type="campaign",
        target_id="1",
        payload_hash="hash-a",
        idempotency_key="email-1",
        status=ActionLog.Status.PLANNED,
    )

    duplicate = ActionLog.objects.filter(
        action_type="email.send_next",
        idempotency_key="email-1",
    ).first()

    assert duplicate == first


@pytest.mark.django_db
def test_action_log_records_result_and_error():
    action = ActionLog.objects.create(
        action_type="task.run_next",
        target_type="task",
        target_id="7",
        payload_hash="hash-b",
        idempotency_key="task-7",
        status=ActionLog.Status.PLANNED,
    )

    action.mark_succeeded({"task_id": 7})
    action.refresh_from_db()
    assert action.status == ActionLog.Status.SUCCEEDED
    assert action.result == {"task_id": 7}

    failed = ActionLog.objects.create(
        action_type="email.send_next",
        target_type="campaign",
        target_id="1",
        payload_hash="hash-c",
        idempotency_key="email-2",
        status=ActionLog.Status.PLANNED,
    )
    failed.mark_failed("no_eligible_email", "No eligible READY_TO_EMAIL deal exists.")
    failed.refresh_from_db()
    assert failed.status == ActionLog.Status.FAILED
    assert failed.error_type == "no_eligible_email"
