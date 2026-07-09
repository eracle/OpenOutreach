import pytest
from django.db import IntegrityError

from openoutreach.core.actions import _execute_planned_action, run_logged_action
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


@pytest.mark.django_db
def test_run_logged_action_dry_run_does_not_execute():
    calls = []

    action, result = run_logged_action(
        action_type="email.send_next",
        target_type="campaign",
        target_id="1",
        payload={"campaign_id": 1},
        idempotency_key="email-dry-run-1",
        dry_run=True,
        execute=lambda: calls.append("sent"),
    )

    assert action.status == ActionLog.Status.PLANNED
    assert result == {"planned": True}
    assert calls == []


@pytest.mark.django_db
def test_run_logged_action_returns_duplicate_without_execution():
    calls = []

    first, first_result = run_logged_action(
        action_type="email.send_next",
        target_type="campaign",
        target_id="1",
        payload={"campaign_id": 1},
        idempotency_key="email-send-1",
        dry_run=False,
        execute=lambda: {"sent": True},
    )
    second, second_result = run_logged_action(
        action_type="email.send_next",
        target_type="campaign",
        target_id="1",
        payload={"campaign_id": 1},
        idempotency_key="email-send-1",
        dry_run=False,
        execute=lambda: calls.append("sent"),
    )

    assert first.status == ActionLog.Status.SUCCEEDED
    assert first_result == {"sent": True}
    assert second.pk == first.pk
    assert second_result == {"duplicate": True, "original_action_id": first.pk}
    assert calls == []


@pytest.mark.django_db
def test_run_logged_action_rejects_conflicting_idempotency_key_without_execution():
    calls = []

    original, original_result = run_logged_action(
        action_type="email.send_next",
        target_type="campaign",
        target_id="1",
        payload={"campaign_id": 1},
        idempotency_key="email-send-conflict-1",
        dry_run=False,
        execute=lambda: {"sent": True},
    )

    with pytest.raises(ValueError, match="idempotency_key conflicts with an existing action"):
        run_logged_action(
            action_type="email.send_next",
            target_type="campaign",
            target_id="2",
            payload={"campaign_id": 2},
            idempotency_key="email-send-conflict-1",
            dry_run=False,
            execute=lambda: calls.append("sent"),
        )

    original.refresh_from_db()
    assert original.status == ActionLog.Status.SUCCEEDED
    assert original_result == {"sent": True}
    assert calls == []


@pytest.mark.django_db
def test_run_logged_action_executes_existing_planned_action_once():
    calls = []

    planned, planned_result = run_logged_action(
        action_type="email.send_next",
        target_type="campaign",
        target_id="1",
        payload={"campaign_id": 1},
        idempotency_key="email-send-plan-1",
        dry_run=True,
        execute=lambda: calls.append("planned"),
    )

    executed, executed_result = run_logged_action(
        action_type="email.send_next",
        target_type="campaign",
        target_id="1",
        payload={"campaign_id": 1},
        idempotency_key="email-send-plan-1",
        dry_run=False,
        execute=lambda: calls.append("sent") or {"sent": True},
    )

    assert planned_result == {"planned": True}
    assert executed.pk == planned.pk
    assert executed.status == ActionLog.Status.SUCCEEDED
    assert executed_result == {"sent": True}
    assert calls == ["sent"]


@pytest.mark.django_db
def test_run_logged_action_marks_failed_and_reraises_execute_error():
    with pytest.raises(RuntimeError, match="boom"):
        run_logged_action(
            action_type="email.send_next",
            target_type="campaign",
            target_id="1",
            payload={"campaign_id": 1},
            idempotency_key="email-send-fail-1",
            dry_run=False,
            execute=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )

    action = ActionLog.objects.get(
        action_type="email.send_next",
        idempotency_key="email-send-fail-1",
    )
    assert action.status == ActionLog.Status.FAILED
    assert action.result is None
    assert action.error_type == "RuntimeError"
    assert action.error_message == "boom"


@pytest.mark.django_db
def test_execute_planned_action_returns_duplicate_when_claim_is_lost():
    action = ActionLog.objects.create(
        action_type="email.send_next",
        target_type="campaign",
        target_id="1",
        payload_hash="hash-a",
        idempotency_key="email-send-claim-1",
        status=ActionLog.Status.PLANNED,
    )
    stale_action = ActionLog.objects.get(pk=action.pk)
    ActionLog.objects.filter(pk=action.pk).update(status=ActionLog.Status.RUNNING)

    calls = []
    result_action, result = _execute_planned_action(
        stale_action,
        execute=lambda: calls.append("sent") or {"sent": True},
    )

    assert result_action.pk == action.pk
    assert result_action.status == ActionLog.Status.RUNNING
    assert result == {"duplicate": True, "original_action_id": action.pk}
    assert calls == []


def test_run_logged_action_rejects_whitespace_idempotency_key():
    with pytest.raises(ValueError, match="idempotency_key is required"):
        run_logged_action(
            action_type="email.send_next",
            target_type="campaign",
            target_id="1",
            payload={"campaign_id": 1},
            idempotency_key="   ",
            dry_run=True,
            execute=lambda: {"planned": False},
        )
