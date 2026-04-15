# linkedin/tasks/scheduler.py
"""Single source of truth for Task row creation.

The daemon's task queue is a reflection of CRM state. Handlers execute
work; this module decides *what task row should exist next* and makes it
so. No other module creates Task rows.

Three layers:

1. **Low-level enqueue** — ``enqueue_connect``, ``enqueue_check_pending``,
   ``enqueue_follow_up``. Insert a PENDING Task row, deduplicating against
   existing PENDING rows with the same key. Called for in-state
   continuations (connect loop, follow-up retries, rate-limit waits).

2. **State-transition hook** — ``on_deal_state_entered(deal)``. Called by
   ``set_profile_state`` after a Deal is saved. Looks at the new state and
   enqueues the appropriate next task, if any. This is what makes the
   pipeline move without handlers calling enqueue themselves.

3. **Reconcile** — ``reconcile(session)``. Walks CRM state and ensures the
   Task table reflects it: one connect task per campaign, one
   check_pending per PENDING deal, one follow_up per CONNECTED deal.
   Recovers stale RUNNING tasks. Runs on daemon startup and whenever the
   queue has no ready task — this is the retry mechanism for crashed
   handlers.
"""
from __future__ import annotations

import datetime
import logging
import random
from datetime import timedelta

from django.utils import timezone

from linkedin.conf import CAMPAIGN_CONFIG
from linkedin.enums import ProfileState
from linkedin.models import Task

logger = logging.getLogger(__name__)


# ── Low-level enqueue ─────────────────────────────────────────────────


def _insert_task(
    task_type: "Task.TaskType",
    payload: dict,
    delay_seconds: float,
    dedup_keys: list[str] | None = None,
) -> bool:
    """Insert a PENDING Task row, skipping if a duplicate already exists.

    Duplicate = same ``task_type``, status=PENDING, and matching payload on
    ``dedup_keys`` (defaults to all payload keys). Returns True if a row
    was inserted.
    """
    filter_kwargs = {
        "task_type": task_type,
        "status": Task.Status.PENDING,
    }
    for key in (dedup_keys if dedup_keys is not None else payload):
        filter_kwargs[f"payload__{key}"] = payload[key]

    if Task.objects.filter(**filter_kwargs).exists():
        return False

    Task.objects.create(
        task_type=task_type,
        scheduled_at=timezone.now() + timedelta(seconds=delay_seconds),
        payload=payload,
    )
    return True


def enqueue_connect(campaign_id: int, delay_seconds: float = 10) -> None:
    """Enqueue a connect task for the given campaign."""
    _insert_task(
        task_type=Task.TaskType.CONNECT,
        payload={"campaign_id": campaign_id},
        delay_seconds=delay_seconds,
    )


def enqueue_check_pending(
    campaign_id: int,
    public_id: str,
    backoff_hours: float,
) -> float:
    """Enqueue a check_pending task with equal-jitter backoff.

    Delay is uniform over ``[backoff_hours/2, backoff_hours]``. Returns
    the chosen delay in hours (for logging).
    """
    half = backoff_hours / 2
    delay_hours = half + random.uniform(0, half)

    _insert_task(
        task_type=Task.TaskType.CHECK_PENDING,
        payload={
            "campaign_id": campaign_id,
            "public_id": public_id,
            "backoff_hours": backoff_hours,
        },
        delay_seconds=delay_hours * 3600,
        dedup_keys=["campaign_id", "public_id"],
    )
    return delay_hours


def enqueue_follow_up(
    campaign_id: int,
    public_id: str,
    delay_seconds: float = 10,
) -> None:
    """Enqueue a follow-up task for a CONNECTED profile."""
    _insert_task(
        task_type=Task.TaskType.FOLLOW_UP,
        payload={"campaign_id": campaign_id, "public_id": public_id},
        delay_seconds=delay_seconds,
        dedup_keys=["campaign_id", "public_id"],
    )


# ── Delay helpers ─────────────────────────────────────────────────────


def seconds_until_tomorrow() -> float:
    """Seconds until 00:00 local time — used for daily rate-limit waits."""
    now = timezone.now()
    tomorrow = (now + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    return (tomorrow - now).total_seconds()


# ── State-transition hook ─────────────────────────────────────────────


def on_deal_state_entered(deal) -> None:
    """Enqueue the task implied by the Deal's current state, if any.

    Called by ``set_profile_state`` after the Deal row is saved. Idempotent:
    relies on enqueue dedup so repeated calls produce at most one pending
    task per (campaign, public_id).
    """
    state = ProfileState(deal.state)
    campaign_id = deal.campaign_id
    public_id = deal.lead.public_identifier

    if not public_id:
        return

    if state == ProfileState.PENDING:
        backoff = deal.backoff_hours or CAMPAIGN_CONFIG["check_pending_recheck_after_hours"]
        enqueue_check_pending(campaign_id, public_id, backoff_hours=backoff)
    elif state == ProfileState.CONNECTED:
        enqueue_follow_up(campaign_id, public_id)
    # Other states (QUALIFIED, READY_TO_CONNECT, COMPLETED, FAILED) have
    # no implied deal-level task — handled by the connect loop or terminal.


# ── Reconciliation ────────────────────────────────────────────────────


def _recover_stale_running_tasks() -> int:
    """Reset RUNNING tasks to PENDING. RUNNING rows can only linger if the
    daemon crashed mid-task, so they are always stale at reconcile time."""
    count = Task.objects.filter(status=Task.Status.RUNNING).update(
        status=Task.Status.PENDING,
    )
    if count:
        logger.info("Recovered %d stale running tasks", count)
    return count


def _seed_connect_tasks(session) -> None:
    """Ensure every campaign has a pending connect task."""
    for campaign in session.campaigns:
        delay = CAMPAIGN_CONFIG["connect_delay_seconds"] if campaign.is_freemium else 0
        enqueue_connect(campaign.pk, delay_seconds=delay)


def _seed_deal_tasks(session) -> None:
    """Ensure every active Deal has the task its state implies.

    Iterates PENDING and CONNECTED deals once per campaign, letting
    ``on_deal_state_entered`` decide what to enqueue (with dedup).
    """
    from crm.models import Deal

    active_states = (ProfileState.PENDING, ProfileState.CONNECTED)
    for campaign in session.campaigns:
        deals = Deal.objects.filter(
            state__in=active_states,
            campaign=campaign,
        ).select_related("lead")
        for deal in deals:
            on_deal_state_entered(deal)


def reconcile(session) -> None:
    """Reconcile the Task queue with CRM state.

    Runs on daemon startup and when the queue drains. This is the safety
    net that re-creates tasks for deals whose handlers crashed (leaving a
    FAILED task with no successor).
    """
    _recover_stale_running_tasks()
    _seed_connect_tasks(session)
    _seed_deal_tasks(session)

    pending_count = Task.objects.pending().count()
    logger.info("Task queue reconciled: %d pending tasks", pending_count)
