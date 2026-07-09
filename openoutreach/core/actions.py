from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from django.db import IntegrityError, transaction

from openoutreach.core.models import ActionLog


def run_logged_action(
    *,
    action_type: str,
    target_type: str,
    target_id: str,
    payload: dict[str, Any],
    idempotency_key: str,
    dry_run: bool,
    execute: Callable[[], dict[str, Any] | None],
) -> tuple[ActionLog, dict[str, Any]]:
    if not idempotency_key or not idempotency_key.strip():
        raise ValueError("idempotency_key is required")

    payload_hash = _payload_hash(payload)
    existing = ActionLog.objects.filter(
        action_type=action_type,
        idempotency_key=idempotency_key,
    ).first()
    if existing:
        _validate_existing_action(
            existing=existing,
            target_type=target_type,
            target_id=target_id,
            payload_hash=payload_hash,
        )
        if existing.status == ActionLog.Status.PLANNED:
            if dry_run:
                return existing, {"planned": True, "duplicate": True, "original_action_id": existing.pk}
            return _execute_planned_action(existing, execute)
        return existing, {"duplicate": True, "original_action_id": existing.pk}

    try:
        with transaction.atomic():
            action = ActionLog.objects.create(
                action_type=action_type,
                target_type=target_type,
                target_id=target_id,
                payload_hash=payload_hash,
                idempotency_key=idempotency_key,
                status=ActionLog.Status.PLANNED,
            )
    except IntegrityError:
        existing = ActionLog.objects.get(
            action_type=action_type,
            idempotency_key=idempotency_key,
        )
        _validate_existing_action(
            existing=existing,
            target_type=target_type,
            target_id=target_id,
            payload_hash=payload_hash,
        )
        if existing.status == ActionLog.Status.PLANNED:
            if dry_run:
                return existing, {"planned": True, "duplicate": True, "original_action_id": existing.pk}
            return _execute_planned_action(existing, execute)
        return existing, {"duplicate": True, "original_action_id": existing.pk}

    if dry_run:
        return action, {"planned": True}

    return _execute_planned_action(action, execute)


def _payload_hash(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _validate_existing_action(
    *,
    existing: ActionLog,
    target_type: str,
    target_id: str,
    payload_hash: str,
) -> None:
    if (
        existing.target_type != target_type
        or existing.target_id != target_id
        or existing.payload_hash != payload_hash
    ):
        raise ValueError("idempotency_key conflicts with an existing action")


def _execute_planned_action(
    action: ActionLog,
    execute: Callable[[], dict[str, Any] | None],
) -> tuple[ActionLog, dict[str, Any]]:
    action.status = ActionLog.Status.RUNNING
    action.save(update_fields=["status", "updated_at"])

    try:
        result = execute() or {}
    except Exception as exc:
        action.mark_failed(exc.__class__.__name__, str(exc))
        raise

    action.mark_succeeded(result)
    action.refresh_from_db()
    return action, result
