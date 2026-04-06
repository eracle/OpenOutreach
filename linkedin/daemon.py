# linkedin/daemon.py
from __future__ import annotations

import logging
import random
import re
import time
import traceback
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from termcolor import colored

from linkedin.conf import (
    ACTIVE_END_HOUR,
    ACTIVE_START_HOUR,
    ACTIVE_TIMEZONE,
    CAMPAIGN_CONFIG,
    ENABLE_ACTIVE_HOURS,
    REST_DAYS,
)
from linkedin.diagnostics import failure_diagnostics
from linkedin.ml.qualifier import BayesianQualifier
from linkedin.models import Task
from linkedin.tasks.check_pending import handle_check_pending
from linkedin.tasks.connect import enqueue_check_pending, enqueue_connect, enqueue_follow_up, handle_connect
from linkedin.tasks.follow_up import handle_follow_up

logger = logging.getLogger(__name__)

_HANDLERS = {
    Task.TaskType.CONNECT: handle_connect,
    Task.TaskType.CHECK_PENDING: handle_check_pending,
    Task.TaskType.FOLLOW_UP: handle_follow_up,
}

# Patterns that indicate a transient error worth retrying
_RETRYABLE_PATTERNS = re.compile(
    r"429|RESOURCE_EXHAUSTED|rate.?limit|quota.?exceeded|503|502|504"
    r"|timeout|timed?\s*out|connection.?reset|connection.?refused",
    re.IGNORECASE,
)
_DEFAULT_RETRY_DELAY = 60  # seconds
_MAX_TASK_RETRIES = 3


def _parse_retry_delay(error_text: str) -> float:
    """Extract 'retry in Ns' from error text, or return default."""
    m = re.search(r"retry\s+in\s+(\d+(?:\.\d+)?)\s*s", error_text, re.IGNORECASE)
    if m:
        return float(m.group(1)) + 5  # add buffer
    return _DEFAULT_RETRY_DELAY


def _is_retryable(error_text: str) -> bool:
    """Check if an error is transient and worth retrying."""
    return bool(_RETRYABLE_PATTERNS.search(error_text))


# Maps raw error patterns to user-friendly messages shown in the CRM UI.
_FRIENDLY_ERRORS = [
    (re.compile(r"429|RESOURCE_EXHAUSTED|quota.?exceeded|rate.?limit", re.I),
     "LLM API quota exhausted. Your API plan's request limit has been reached. "
     "The daemon will auto-retry — or try again in a few hours."),
    (re.compile(r"401|403|UNAUTHENTICATED|PERMISSION_DENIED|invalid.?api.?key", re.I),
     "LLM API key is invalid or expired. Go to Settings and update your API key."),
    (re.compile(r"timeout|timed?\s*out", re.I),
     "Request timed out. The LLM provider may be slow. The daemon will auto-retry."),
    (re.compile(r"502|503|504|UNAVAILABLE", re.I),
     "LLM provider is temporarily unavailable. The daemon will auto-retry."),
    (re.compile(r"connection.?refused|connection.?reset", re.I),
     "Could not connect to the LLM provider. Check your internet connection."),
]


def friendly_error(raw_error: str) -> str:
    """Convert a raw traceback into a short, user-friendly message."""
    for pattern, message in _FRIENDLY_ERRORS:
        if pattern.search(raw_error):
            return message
    # For unknown errors, show just the last line (the actual exception)
    lines = raw_error.strip().splitlines()
    return lines[-1] if lines else raw_error


def _build_qualifiers(campaigns, cfg):
    """Create a qualifier for every campaign, keyed by campaign PK."""
    from crm.models import Lead

    qualifiers: dict[int, BayesianQualifier] = {}
    for campaign in campaigns:
        q = BayesianQualifier(
            seed=42,
            n_mc_samples=cfg["qualification_n_mc_samples"],
            campaign=campaign,
        )
        X, y = Lead.get_labeled_arrays(campaign)
        if len(X) > 0:
            q.warm_start(X, y)
            logger.info(
                colored("GP qualifier warm-started", "cyan")
                + " on %d labelled samples (%d positive, %d negative)"
                + " for campaign %s",
                len(y), int((y == 1).sum()), int((y == 0).sum()), campaign,
            )
        qualifiers[campaign.pk] = q

    return qualifiers


# ------------------------------------------------------------------
# Active-hours schedule guard
# ------------------------------------------------------------------


def seconds_until_active() -> float:
    """Return seconds to wait before the next active window, or 0 if active now."""
    if not ENABLE_ACTIVE_HOURS:
        return 0.0
    tz = ZoneInfo(ACTIVE_TIMEZONE)
    now = timezone.localtime(timezone=tz)

    if now.weekday() not in REST_DAYS and ACTIVE_START_HOUR <= now.hour < ACTIVE_END_HOUR:
        return 0.0

    # Find the next active start: try today first, then subsequent days
    candidate = timezone.make_aware(
        now.replace(hour=ACTIVE_START_HOUR, minute=0, second=0, microsecond=0, tzinfo=None),
        timezone=tz,
    )
    if candidate <= now:
        candidate += timedelta(days=1)
    while candidate.weekday() in REST_DAYS:
        candidate += timedelta(days=1)
    return (candidate - now).total_seconds()


# ------------------------------------------------------------------
# Task queue worker
# ------------------------------------------------------------------


def heal_tasks(session):
    """Reconcile task queue with CRM state on daemon startup.

    1. Reset stale 'running' tasks to 'pending' (crashed worker recovery)
    2. Remove pending tasks for campaigns this profile no longer belongs to
    3. Seed one 'connect' task per campaign if none pending
    4. Create 'check_pending' tasks for PENDING profiles without tasks
    5. Create 'follow_up' tasks for CONNECTED profiles without tasks
    """
    from crm.models import Deal
    from linkedin.url_utils import url_to_public_id
    from linkedin.enums import ProfileState

    cfg = CAMPAIGN_CONFIG

    # 1. Recover stale running tasks
    stale_count = Task.objects.filter(status=Task.Status.RUNNING).update(
        status=Task.Status.PENDING,
    )
    if stale_count:
        logger.info("Recovered %d stale running tasks", stale_count)

    # 2. Remove pending/failed tasks for campaigns not in current session
    active_campaign_pks = {c.pk for c in session.campaigns}
    if active_campaign_pks:
        orphan_tasks = Task.objects.filter(
            status__in=[Task.Status.PENDING, Task.Status.FAILED],
        ).exclude(payload__campaign_id__in=list(active_campaign_pks))
        orphan_count = orphan_tasks.count()
        if orphan_count:
            orphan_tasks.delete()
            logger.info("Removed %d orphan tasks for inactive campaigns", orphan_count)

    # 3. Seed connect tasks per campaign
    for campaign in session.campaigns:
        enqueue_connect(campaign.pk)

    # 4. Check_pending tasks for PENDING profiles
    for campaign in session.campaigns:
        session.campaign = campaign
        pending_deals = Deal.objects.filter(
            state=ProfileState.PENDING,
            campaign=campaign,
        ).select_related("lead")

        for deal in pending_deals:
            public_id = url_to_public_id(deal.lead.linkedin_url) if deal.lead.linkedin_url else None
            if not public_id:
                continue
            backoff = deal.backoff_hours or cfg["check_pending_recheck_after_hours"]
            enqueue_check_pending(campaign.pk, public_id, backoff_hours=backoff)

    # 5. Follow_up tasks for CONNECTED profiles
    for campaign in session.campaigns:
        session.campaign = campaign
        connected_deals = Deal.objects.filter(
            state=ProfileState.CONNECTED,
            campaign=campaign,
        ).select_related("lead")

        for deal in connected_deals:
            public_id = url_to_public_id(deal.lead.linkedin_url) if deal.lead.linkedin_url else None
            if not public_id:
                continue
            enqueue_follow_up(campaign.pk, public_id, delay_seconds=random.uniform(5, 60))

    pending_count = Task.objects.pending().count()
    logger.info("Task queue healed: %d pending tasks", pending_count)


def run_daemon(session, stop_event=None):
    from linkedin.models import Campaign

    cfg = CAMPAIGN_CONFIG

    qualifiers = _build_qualifiers(session.campaigns, cfg)

    # Startup healing
    heal_tasks(session)

    campaigns = session.campaigns
    if not campaigns:
        logger.error("No campaigns found — cannot start daemon")
        return

    logger.info(
        colored("Daemon started", "green", attrs=["bold"])
        + " — %d campaigns, task queue worker",
        len(campaigns),
    )

    # Single-threaded: one task at a time, no concurrent enqueuing,
    # so sleeping until the next scheduled_at is safe.
    while not (stop_event and stop_event.is_set()):
        pause = seconds_until_active()
        if pause > 0:
            h, m = int(pause // 3600), int(pause % 3600 // 60)
            logger.info("Outside active hours — sleeping %dh%02dm", h, m)
            if stop_event and stop_event.wait(pause):
                break
            else:
                time.sleep(pause) if not stop_event else None
            continue

        task = Task.objects.claim_next()
        if task is None:
            wait = Task.objects.seconds_to_next()
            if wait is None:
                if stop_event:
                    # Web-started daemon: sleep and re-check instead of exiting
                    logger.info("Queue empty — waiting for new tasks")
                    if stop_event.wait(30):
                        break
                    continue
                logger.info("Queue empty — nothing to do")
                return
            if wait > 0:
                h, m = int(wait // 3600), int(wait % 3600 // 60)
                logger.info("Next task in %dh%02dm — sleeping", h, m)
                if stop_event and stop_event.wait(wait):
                    break
                elif not stop_event:
                    time.sleep(wait)
            continue

        campaign = Campaign.objects.filter(pk=task.payload.get("campaign_id")).first()
        if not campaign:
            task.mark_failed(f"Campaign {task.payload.get('campaign_id')} not found")
            continue

        session.campaign = campaign
        task.mark_running()

        handler = _HANDLERS.get(task.task_type)
        if handler is None:
            task.mark_failed(f"Unknown task type: {task.task_type}")
            continue

        try:
            with failure_diagnostics(session):
                handler(task, session, qualifiers)
        except Exception:
            tb = traceback.format_exc()
            if _is_retryable(tb):
                retries = task.payload.get("_retries", 0)
                if retries < _MAX_TASK_RETRIES:
                    delay = _parse_retry_delay(tb)
                    task.payload["_retries"] = retries + 1
                    task.status = Task.Status.PENDING
                    task.scheduled_at = timezone.now() + timedelta(seconds=delay)
                    task.save(update_fields=["status", "scheduled_at", "payload"])
                    logger.warning(
                        "Task %s hit transient error (retry %d/%d) — rescheduled in %ds",
                        task, retries + 1, _MAX_TASK_RETRIES, int(delay),
                    )
                    continue
            task.mark_failed(friendly_error(tb))
            logger.exception("Task %s failed", task)
            continue

        task.mark_completed()
