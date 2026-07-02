# openoutreach/core/scheduler.py
"""Per-type 24h planner with Poisson-spaced lazy task slots.

The daemon's task queue is *lazy*: each row carries only ``task_type``,
``campaign_id``, and ``scheduled_at``. The handler resolves a concrete
target (lead/deal) at execution time via a single eligibility query.

This module is the only place that creates ``Task`` rows. The pipeline
moves forward in two layers:

1. **Per-type planner** — ``plan_find_email_window``. When no PENDING
   find_email task exists for a campaign, it inserts one row that fires
   immediately and Poisson-spaces the remaining ``n - 1`` rows across the
   working portion of the next 24h window. The leading immediate slot kills
   the cold-start ramp (without it the first action would sit ``T/n`` away
   on average). The email channels do not fake a rhythm — they
   **eager-drain** instead (``flush_email_queue`` for openers,
   ``flush_follow_up_queue`` for due follow-ups).

2. **Reconcile** — ``reconcile(session)``. Recovers stale RUNNING tasks
   and calls each planner per campaign. The daemon invokes it on startup
   and whenever the queue has no ready task.
"""
from __future__ import annotations

import logging
import random
from datetime import datetime as Datetime, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from openoutreach.core.conf import (
    ACTIVE_END_HOUR,
    ACTIVE_START_HOUR,
    ENABLE_ACTIVE_HOURS,
    FIND_EMAIL_DAILY_CAP,
)
from openoutreach.crm.models import DealState
from openoutreach.core.models import Task

logger = logging.getLogger(__name__)


# ── Working-hours arithmetic ──────────────────────────────────────────


def _working_intervals(start, end, tz_name) -> list[tuple]:
    """Return ``[(s, e), ...]`` UTC datetimes for the working portions of
    ``[start, end]``. The whole window ``[(start, end)]`` is returned —
    i.e. no gating — when ``ENABLE_ACTIVE_HOURS`` is False or ``tz_name`` is
    None (timezone not resolved, e.g. unknown profile country)."""
    if not ENABLE_ACTIVE_HOURS or tz_name is None:
        return [(start, end)]

    tz = ZoneInfo(tz_name)
    local_start = start.astimezone(tz)
    local_end = end.astimezone(tz)

    intervals: list[tuple] = []
    day = local_start.date()
    last_day = local_end.date()
    while day <= last_day:
        day_active_start = Datetime(
            day.year, day.month, day.day, ACTIVE_START_HOUR, tzinfo=tz,
        )
        day_active_end = Datetime(
            day.year, day.month, day.day, ACTIVE_END_HOUR, tzinfo=tz,
        )
        s = max(day_active_start, local_start)
        e = min(day_active_end, local_end)
        if e > s:
            intervals.append((s, e))
        day = day + timedelta(days=1)
    return intervals


def working_seconds_in_window(start, end, tz_name) -> float:
    """Sum of seconds inside ``[ACTIVE_START_HOUR, ACTIVE_END_HOUR]`` between
    ``start`` and ``end``. Returns ``(end - start).total_seconds()`` when
    active hours are disabled or ``tz_name`` is None (no gating)."""
    if not ENABLE_ACTIVE_HOURS or tz_name is None:
        return max(0.0, (end - start).total_seconds())
    return sum((e - s).total_seconds() for s, e in _working_intervals(start, end, tz_name))


def poisson_slot_times(now, n: int, tz_name, horizon_hours: float = 24) -> list:
    """Return ``n`` strictly-increasing timestamps inside the working
    portion of ``[now, now + horizon_hours]``.

    Implementation: sample ``n`` uniform positions in ``[0, T)`` (working
    seconds) and sort. This is the order-statistic representation of a
    conditional Poisson process given ``n`` arrivals in the window —
    same distribution as exponential inter-arrival sampling, but
    guarantees exactly ``n`` slots without overshoot. Mean spacing in
    working time is ``T / (n + 1)``.
    """
    if n <= 0:
        return []

    end = now + timedelta(hours=horizon_hours)
    intervals = _working_intervals(now, end, tz_name)
    total = sum((e - s).total_seconds() for s, e in intervals)
    if total <= 0:
        return []

    positions = sorted(random.uniform(0, total) for _ in range(n))

    times: list = []
    cursor_interval = 0
    cursor_offset = 0.0  # working-seconds consumed before the current interval
    for pos in positions:
        while cursor_interval < len(intervals):
            s, e = intervals[cursor_interval]
            dur = (e - s).total_seconds()
            if pos < cursor_offset + dur:
                times.append(s + timedelta(seconds=pos - cursor_offset))
                break
            cursor_offset += dur
            cursor_interval += 1
    return times


# ── Per-type planners ─────────────────────────────────────────────────


def _has_pending(task_type: "Task.TaskType", campaign_id: int) -> bool:
    return Task.objects.filter(
        task_type=task_type,
        status=Task.Status.PENDING,
        payload__campaign_id=campaign_id,
    ).exists()


def _create_lazy_slots(task_type: "Task.TaskType", campaign_id: int, times: list) -> int:
    if not times:
        return 0
    Task.objects.bulk_create([
        Task(
            task_type=task_type,
            scheduled_at=t,
            payload={"campaign_id": campaign_id},
        )
        for t in times
    ])
    return len(times)


def _plan_slots(task_type: "Task.TaskType", campaign_id: int, n: int, tz_name) -> int:
    """Schedule *n* lazy slots: one fires immediately, the remaining
    ``n - 1`` are Poisson-spaced across the next 24h working window
    (``tz_name`` defines that window's local hours; None → full 24h).

    The leading immediate slot is intentional — without it the first
    action of a freshly-planned window would sit ``T/n`` away on average
    (the mean of a single ``Exp(n/T)`` draw). That cold-start ramp made
    `make run` feel dead for ~an hour on a 20/day campaign.
    """
    if n <= 0:
        return 0
    now = timezone.now()
    times = [now] + poisson_slot_times(now, n - 1, tz_name)
    return _create_lazy_slots(task_type, campaign_id, times)


def plan_find_email_window(session, campaign) -> int:
    """Plan the next 24h of find_email slots for *campaign*. No-op when a
    PENDING find_email task already exists, no mailbox is connected, or the
    BetterContact finder is unconfigured.

    ``find_email`` is the paid leg (one credit per verified hit), so the slot
    count is a flat daily spend guard (``FIND_EMAIL_DAILY_CAP``) rather than a
    rate-limit ration — there is no anti-bot rhythm to fake. Gating on finder
    usability keeps the daemon from spinning empty slots when it can't act:
    without a key or a mailbox, every lookup would be a no-op.
    """
    from openoutreach.emails import bettercontact
    from openoutreach.emails.models import has_mailbox

    if _has_pending(Task.TaskType.FIND_EMAIL, campaign.pk):
        return 0
    if not has_mailbox() or not bettercontact.is_configured():
        return 0

    created = _plan_slots(
        Task.TaskType.FIND_EMAIL, campaign.pk, FIND_EMAIL_DAILY_CAP, session.active_timezone,
    )
    if created:
        logger.info(
            "[%s] planned %d find_email slots over next 24h — 1 fires now, "
            "%d Poisson-spaced (cap=%d)",
            campaign, created, max(0, created - 1), FIND_EMAIL_DAILY_CAP,
        )
    return created


# ── Eager drain (no window) ───────────────────────────────────────────


def flush_email_queue(session, campaign) -> int:
    """Drain the READY_TO_EMAIL pool for *campaign* into immediate task slots.

    The eager counterpart to the ``plan_*_window`` planners: those *ration* a
    rate-limited action over a 24h window to fake human rhythm; email has no
    anti-bot rhythm to fake, so every queued deal is emitted as an immediate
    slot (scheduled ``now``, no Poisson spacing, no ranking) and drains
    back-to-back. The only throttle is the pool-wide per-box daily cap
    (``Mailbox.objects.remaining_today()``), re-checked at send time.

    No-op when a PENDING email task already exists, no box has headroom, or the
    pool is empty. Count is scoped to ``campaign.pk`` directly because
    ``reconcile`` does not set ``session.campaign`` before invoking it.
    """
    from openoutreach.crm.models import Deal
    from openoutreach.emails.models import Mailbox

    if _has_pending(Task.TaskType.EMAIL, campaign.pk):
        return 0

    remaining = Mailbox.objects.remaining_today()
    if remaining <= 0:
        return 0

    queued = Deal.objects.filter(
        campaign_id=campaign.pk,
        state=DealState.READY_TO_EMAIL,
        lead__disqualified=False,
    ).count()
    n = min(queued, remaining)
    if n <= 0:
        return 0

    now = timezone.now()
    created = _create_lazy_slots(Task.TaskType.EMAIL, campaign.pk, [now] * n)
    logger.info(
        "[%s] flushed %d email slots to send now (queued=%d, cap_remaining=%d)",
        campaign, created, queued, remaining,
    )
    return created


def flush_follow_up_queue(session, campaign) -> int:
    """Drain due EMAILED deals for *campaign* into immediate follow-up slots.

    The follow-up counterpart to ``flush_email_queue``: a follow-up has no anti-bot
    rhythm to fake, so instead of Poisson-spacing a daily ration (the old LinkedIn
    ``plan_follow_up_window``), every EMAILED deal whose countdown
    (``next_follow_up_at``) is due is emitted as an immediate slot, capped by the
    pool-wide per-box daily headroom (re-checked at send time). Reading the thread
    happens inside the handler at that slot — exactly when the countdown fires.

    No-op when a PENDING follow-up task already exists, no box has headroom, or
    nothing is due.
    """
    from openoutreach.crm.models import Deal
    from openoutreach.emails.models import Mailbox

    if _has_pending(Task.TaskType.FOLLOW_UP, campaign.pk):
        return 0

    remaining = Mailbox.objects.remaining_today()
    if remaining <= 0:
        return 0

    now = timezone.now()
    due = Deal.objects.filter(
        campaign_id=campaign.pk,
        state=DealState.EMAILED,
        outcome="",
        lead__disqualified=False,
        next_follow_up_at__lte=now,
    ).count()
    n = min(due, remaining)
    if n <= 0:
        return 0

    created = _create_lazy_slots(Task.TaskType.FOLLOW_UP, campaign.pk, [now] * n)
    logger.info(
        "[%s] flushed %d follow_up slots to run now (due=%d, cap_remaining=%d)",
        campaign, created, due, remaining,
    )
    return created


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


_PLANNERS = (
    plan_find_email_window,
)


def reconcile(session) -> None:
    """Recover stale RUNNING tasks, then ensure every (campaign, task_type)
    whose pending queue is empty gets a fresh 24h plan. Runs on daemon
    startup and whenever the queue has no ready task."""
    _recover_stale_running_tasks()
    for campaign in session.campaigns:
        for planner in _PLANNERS:
            planner(session, campaign)
        # Eager-drain counterparts to the window planner — queue every ready
        # opener and every due follow-up to run now (paced only by the per-box
        # daily cap; follow-ups additionally gated by their countdown).
        flush_email_queue(session, campaign)
        flush_follow_up_queue(session, campaign)

    pending_count = Task.objects.pending().count()
    logger.info("Task queue reconciled: %d pending tasks", pending_count)
