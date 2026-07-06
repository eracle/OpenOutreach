# openoutreach/core/daemon.py
from __future__ import annotations

import logging
import random
import time
from datetime import timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from django.utils import timezone
from pydantic_ai.exceptions import ModelHTTPError

from termcolor import colored

from openoutreach.core.conf import (
    ACTIVE_END_HOUR,
    ACTIVE_START_HOUR,
    CAMPAIGN_CONFIG,
    ENABLE_ACTIVE_HOURS,
)
from openoutreach.core.ml.qualifier import BayesianQualifier, KitQualifier
from openoutreach.core.models import Task
from openoutreach.emails.tasks.find_email import handle_find_email
from openoutreach.emails.tasks.follow_up import handle_follow_up
from openoutreach.emails.tasks.send import handle_email

logger = logging.getLogger(__name__)

_HANDLERS = {
    Task.TaskType.FIND_EMAIL: handle_find_email,
    Task.TaskType.FOLLOW_UP: handle_follow_up,
    Task.TaskType.EMAIL: handle_email,
}

HEARTBEAT_INTERVAL = 300  # 5 minutes
HEARTBEAT_SLICE = 60      # wake every minute during long sleeps


# ── Heartbeat ────────────────────────────────────────────────────────


class Heartbeat:
    """Logs an ``alive — <context>`` line at most once every *interval* seconds.

    The first call won't log (``_last`` starts at now) — quiet gaps begin
    counting from daemon start, not the Unix epoch.
    """

    def __init__(self, interval: float = HEARTBEAT_INTERVAL):
        self._interval = interval
        self._last = time.monotonic()

    def maybe_log(self, context: str | Callable[[], str]) -> None:
        now = time.monotonic()
        if now - self._last < self._interval:
            return
        self._last = now
        text = context() if callable(context) else context
        logger.info(colored("alive", "cyan") + " — %s", text)


def _hm(seconds: float) -> str:
    """Format a duration as ``Hh MMm`` (e.g. ``0h08m``)."""
    h, m = int(seconds // 3600), int(seconds % 3600 // 60)
    return f"{h}h{m:02d}m"


def sleep_with_heartbeat(
    seconds: float, heartbeat: Heartbeat, context: str | Callable[[float], str]
) -> None:
    """``time.sleep(seconds)`` that wakes every ``HEARTBEAT_SLICE`` seconds to
    let *heartbeat* fire. Use for any idle sleep longer than the heartbeat
    interval so the daemon never goes silent for more than 5 minutes.

    *context* is either a fixed string or a callable taking the live remaining
    seconds — pass a callable for a heartbeat that counts down instead of
    replaying a frozen label.
    """
    end = time.monotonic() + seconds
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(HEARTBEAT_SLICE, remaining))
        if callable(context):
            heartbeat.maybe_log(lambda: context(max(0.0, end - time.monotonic())))
        else:
            heartbeat.maybe_log(context)


# ── Human-rhythm pacing ──────────────────────────────────────────────


class _HumanRhythmBreak:
    """Wall-clock burst timer that injects a random break between bursts.

    Call ``reset()`` after idle sleeps (active-hours pause, waiting for
    the next scheduled task) so the burst timer tracks real work, not
    wall-clock. Call ``maybe_break()`` after each successful task —
    it sleeps a random break duration when the current burst is done.
    """

    def __init__(self, heartbeat: Heartbeat):
        self._heartbeat = heartbeat
        self._new_burst()

    def _new_burst(self):
        self._burst_start = time.monotonic()
        self._burst_duration = random.uniform(
            CAMPAIGN_CONFIG["burst_min_seconds"],
            CAMPAIGN_CONFIG["burst_max_seconds"],
        )

    def reset(self):
        """Start a fresh burst without taking a break. Use after idle gaps."""
        self._new_burst()

    def maybe_break(self):
        """Sleep a random break and start a new burst if the current one is done."""
        if time.monotonic() - self._burst_start < self._burst_duration:
            return
        break_seconds = random.uniform(
            CAMPAIGN_CONFIG["break_min_seconds"],
            CAMPAIGN_CONFIG["break_max_seconds"],
        )
        logger.info("Taking a %dm break", int(break_seconds // 60))
        sleep_with_heartbeat(
            break_seconds,
            self._heartbeat,
            f"on break, {int(break_seconds // 60)}m total",
        )
        self._new_burst()


def _build_qualifiers(campaigns, cfg, kit_model=None):
    """Create a qualifier for every campaign, keyed by campaign PK.

    Freemium campaigns use the pre-trained kit model (``KitQualifier``) when one
    is available; every other campaign gets a warm-started GP qualifier.
    """
    from openoutreach.crm.models import Lead

    qualifiers: dict[int, BayesianQualifier | KitQualifier] = {}
    for campaign in campaigns:
        if campaign.is_freemium:
            if kit_model is None:
                continue
            qualifiers[campaign.pk] = KitQualifier(kit_model)
            continue

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


def seconds_until_active(tz_name: str | None) -> float:
    """Return seconds to wait before the next active window, or 0 if active now.

    Single contiguous daily window — no weekend skip. Returns 0 (never gate)
    when active hours are disabled or ``tz_name`` is None — the timezone is
    resolved from the operator's onboarding country, and an unknown country
    leaves it None rather than guessing UTC.
    """
    if not ENABLE_ACTIVE_HOURS or tz_name is None:
        return 0.0
    tz = ZoneInfo(tz_name)
    now = timezone.localtime(timezone=tz)

    if ACTIVE_START_HOUR <= now.hour < ACTIVE_END_HOUR:
        return 0.0

    candidate = timezone.make_aware(
        now.replace(hour=ACTIVE_START_HOUR, minute=0, second=0, microsecond=0, tzinfo=None),
        timezone=tz,
    )
    if candidate <= now:
        candidate += timedelta(days=1)
    return (candidate - now).total_seconds()


# ------------------------------------------------------------------
# Task queue worker
# ------------------------------------------------------------------


def run_daemon(session):
    from openoutreach.core.models import Campaign

    cfg = CAMPAIGN_CONFIG

    # Load the pre-trained kit for freemium campaigns: create/refresh the freemium
    # Campaign, seed its leads, and hand the kit model to the qualifier builder.
    # Seed embeddings come from Lead-Finder discovery, so freshly-seeded leads stay
    # dormant in the kit-ranked pool until discovery embeds them.
    from openoutreach.core.ml.hub import fetch_kit
    from openoutreach.core.setup.freemium import import_freemium_campaign, seed_profiles

    kit = fetch_kit()
    if kit:
        freemium_campaign = import_freemium_campaign(kit["config"])
        if freemium_campaign:
            prev_campaign = session.campaign
            session.campaign = freemium_campaign
            seed_profiles(session, kit["config"])
            session.campaign = prev_campaign
            # The freemium campaign was just linked to the operator — drop the
            # cached campaign list so it's picked up below.
            session.__dict__.pop("campaigns", None)

    qualifiers = _build_qualifiers(
        session.campaigns, cfg, kit_model=kit["model"] if kit else None,
    )

    campaigns = session.campaigns
    if not campaigns:
        logger.error("No campaigns found — cannot start daemon")
        return

    logger.info(
        colored("Daemon started", "green", attrs=["bold"])
        + " — %d campaigns, task queue worker",
        len(campaigns),
    )

    if ENABLE_ACTIVE_HOURS:
        logger.info(
            "Active hours %02d:00–%02d:00 — timezone %s",
            ACTIVE_START_HOUR, ACTIVE_END_HOUR, session.active_timezone_provenance(),
        )
    else:
        logger.info("Active hours disabled — running 24/7")

    heartbeat = Heartbeat()
    rhythm = _HumanRhythmBreak(heartbeat)

    # Startup reconcile: recover any tasks a prior crash left RUNNING and flush
    # every ready email into an immediate slot before serving the queue. Paired
    # with email-first claim ordering (Task.pending), this makes the first thing
    # the daemon does on startup send any email it can.
    from openoutreach.core.scheduler import reconcile
    reconcile(session)

    # Single-threaded: one task at a time, no concurrent enqueuing,
    # so sleeping until the next scheduled_at is safe.
    while True:
        pause = seconds_until_active(session.active_timezone)
        if pause > 0:
            logger.info(
                "Outside active hours (%02d:00–%02d:00 %s) — next window in %s",
                ACTIVE_START_HOUR, ACTIVE_END_HOUR,
                session.active_timezone, _hm(pause),
            )
            sleep_with_heartbeat(
                pause, heartbeat,
                lambda left: f"outside active hours, {_hm(left)} left",
            )
            rhythm.reset()
            continue

        task = Task.objects.claim_next()
        if task is None:
            # Nothing ready — reconcile the queue from CRM state. Any deal
            # stuck without a pending task (e.g. because a prior handler
            # crashed) gets a fresh task here; this is the retry mechanism.
            from openoutreach.core.scheduler import reconcile
            reconcile(session)

            wait = Task.objects.seconds_to_next()
            if wait is None:
                logger.info("Queue empty after reconcile — sleeping 1h")
                sleep_with_heartbeat(3600, heartbeat, "queue empty")
                rhythm.reset()
                continue
            if wait > 0:
                logger.info("Next task in %s — sleeping", _hm(wait))
                sleep_with_heartbeat(
                    wait, heartbeat, lambda left: f"next task in {_hm(left)}",
                )
                rhythm.reset()
            continue

        campaign = Campaign.objects.filter(pk=task.payload.get("campaign_id")).first()
        if not campaign:
            logger.error("Campaign %s not found", task.payload.get("campaign_id"))
            task.mark_failed()
            continue

        session.campaign = campaign
        task.mark_running()

        handler = _HANDLERS.get(task.task_type)
        if handler is None:
            logger.error("Unknown task type: %s", task.task_type)
            task.mark_failed()
            continue

        try:
            handler(task, session, qualifiers)
        except ModelHTTPError as e:
            task.mark_failed()
            logger.error(
                colored("Daemon stopped — LLM API error", "red", attrs=["bold"])
                + "\n%s\nCheck ai_model (provider:model), llm_api_key, and llm_api_base in Admin → Site Configuration.", e,
            )
            return
        except Exception:
            task.mark_failed()
            logger.exception("Task %s failed", task)
            continue

        task.mark_completed()
        rhythm.maybe_break()
