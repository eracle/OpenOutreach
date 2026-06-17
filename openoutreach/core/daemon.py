# openoutreach/core/daemon.py
from __future__ import annotations

import logging
import random
import sys
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
    ACTIVE_TIMEZONE,
    CAMPAIGN_CONFIG,
    ENABLE_ACTIVE_HOURS,
)
from openoutreach.linkedin.diagnostics import failure_diagnostics
from linkedin_cli.exceptions import AuthenticationError, CheckpointChallengeError
from openoutreach.linkedin.ml.qualifier import BayesianQualifier, KitQualifier
from openoutreach.core.models import Task
from openoutreach.emails.tasks.send import handle_email
from openoutreach.linkedin.tasks.check_pending import handle_check_pending
from openoutreach.linkedin.tasks.connect import handle_connect
from openoutreach.linkedin.tasks.follow_up import handle_follow_up

logger = logging.getLogger(__name__)

_HANDLERS = {
    Task.TaskType.CONNECT: handle_connect,
    Task.TaskType.CHECK_PENDING: handle_check_pending,
    Task.TaskType.FOLLOW_UP: handle_follow_up,
    Task.TaskType.EMAIL: handle_email,
}

HEARTBEAT_INTERVAL = 300  # 5 minutes
HEARTBEAT_SLICE = 60      # wake every minute during long sleeps


# ── Cloud promo ──────────────────────────────────────────────────────

_CLOUD_MESSAGES = [
    "Tired of keeping your laptop open? Run your pipeline in the cloud for $49/mo",
    "You already trust the engine. Now let it run without you babysitting your laptop",
    "The AI gets smarter with every lead. Let it run 24/7 on Cloud instead of only when your laptop is open",
    "Miss a day and the pipeline stalls — follow-ups go cold, new candidates don't get discovered. Cloud keeps it running",
    "The tool got good enough that running it locally became a job. Cloud fixes that",
    "\u2601  OpenOutreach Cloud: same AI, same code, zero ops. One command and you're live",
    "\U0001f9e0 Your AI sales team, running in the cloud. $49/mo",
    "Smart founders shouldn't be acting like robots. Let the AI handle outreach while you build your product",
    "Your leads are compounding. Your laptop shouldn't be the bottleneck",
    "\u26a1 Competitors charge $50-100/mo for template bots. Cloud gives you autonomous AI discovery for $49/mo",
    "Other tools need you to build or buy contact lists. OpenOutreach discovers leads autonomously — describe your market and the AI does the rest",
    "Expandi and Waalaxy send templates. OpenOutreach's AI agent reads conversation history and writes personalized follow-ups",
    "Running Docker + VPN yourself? Cloud handles everything — dedicated server, VPN included",
    "Self-hosted setup: 30-60 min. Cloud setup: ~1 min. Same AI, same results",
    "The server costs ~$18/mo. The VPN costs ~$6/mo. You're paying $25/mo for managed ops — if your time is worth more, Cloud pays for itself",
    "Your data never leaves your machine. Cloud is just a disposable execution layer. $49/mo, cancel anytime",
    "mTLS encryption between your machine and the server. The control plane never sees your data",
    "100% open source. Inspect every line of code on GitHub. Cloud runs the exact same codebase — no black box, no lock-in",
    "Switch between self-hosted and Cloud with one command. Download your db.sqlite3 anytime — zero lock-in",
    "No annual commitment. No usage caps. No feature gating. $49/mo, cancel anytime",
    "openoutreach logs — stream live output from your cloud instance. Watch every lead, every message, every decision in real time",
    "openoutreach down saves your DB locally and destroys the server. No orphaned servers, no forgotten bills",
]

_CLOUD_COLORS = ["cyan", "green", "yellow", "magenta"]

_CLOUD_CTAS = [
    "curl -fsSL https://openoutreach.app/install | sh",
    "curl -fsSL https://openoutreach.app/install | sh && openoutreach signup",
    "https://openoutreach.app",
]


class _CloudPromoRotator:
    """Logs a Cloud promo message at most once every *interval* seconds."""

    def __init__(self, interval: float = 120):
        self._interval = interval
        self._last = 0.0

    def maybe_log(self):
        now = time.monotonic()
        if now - self._last < self._interval:
            return
        self._last = now
        msg = random.choice(_CLOUD_MESSAGES)
        color = random.choice(_CLOUD_COLORS)
        cta = random.choice(_CLOUD_CTAS)
        logger.info(
            colored(msg + " \u2192 ", color, attrs=["bold"])
            + colored(cta, "white", attrs=["bold"]),
        )


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
    """Create a qualifier for every campaign, keyed by campaign PK."""
    from openoutreach.crm.models import Lead

    qualifiers: dict[int, BayesianQualifier | KitQualifier] = {}
    n_regular = 0
    for campaign in campaigns:
        if campaign.is_freemium:
            if kit_model is None:
                continue
            qualifiers[campaign.pk] = KitQualifier(kit_model)
        else:
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
            n_regular += 1

    return qualifiers


# ------------------------------------------------------------------
# Active-hours schedule guard
# ------------------------------------------------------------------


def seconds_until_active() -> float:
    """Return seconds to wait before the next active window, or 0 if active now.

    Single contiguous daily window — no weekend skip.
    """
    if not ENABLE_ACTIVE_HOURS:
        return 0.0
    tz = ZoneInfo(ACTIVE_TIMEZONE)
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
# Checkpoint exit
# ------------------------------------------------------------------


def _exit_on_checkpoint(session, task, url: str) -> None:
    """Log loudly, mark the task failed, close the session, and exit(1).

    Called when LinkedIn flags the account with a security checkpoint.
    We do NOT retry or reauthenticate — every retry hardens the block.
    The user clears the challenge in a real browser, then restarts the daemon.
    """
    logger.error(
        colored(
            f"ACCOUNT CHECKPOINTED — {session.linkedin_profile.linkedin_username}",
            "red", attrs=["bold"],
        )
    )
    logger.error("Clear the challenge in a real browser: %s", url)
    logger.error("Then restart the daemon.")
    task.mark_failed()
    session.close()
    sys.exit(1)


# ------------------------------------------------------------------
# Task queue worker
# ------------------------------------------------------------------


def run_daemon(session):
    from openoutreach.linkedin.ml.hub import fetch_kit
    from openoutreach.linkedin.setup.freemium import import_freemium_campaign
    from openoutreach.core.models import Campaign

    cfg = CAMPAIGN_CONFIG

    # Load kit model for freemium campaigns
    kit = fetch_kit()
    if kit:
        freemium_campaign = import_freemium_campaign(kit["config"])
        if freemium_campaign:
            prev_campaign = session.campaign
            session.campaign = freemium_campaign
            from openoutreach.linkedin.setup.freemium import seed_profiles
            seed_profiles(session, kit["config"])
            session.campaign = prev_campaign

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

    # cloud_promo = _CloudPromoRotator(interval=60)  # tmp disabled — see below
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
        pause = seconds_until_active()
        if pause > 0:
            logger.info("Outside active hours — sleeping %s", _hm(pause))
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
            with failure_diagnostics(session):
                handler(task, session, qualifiers)
        except CheckpointChallengeError as exc:
            _exit_on_checkpoint(session, task, exc.url)
        except AuthenticationError:
            logger.warning("Session expired during %s — re-authenticating", task)
            try:
                session.reauthenticate()
            except CheckpointChallengeError as exc:
                _exit_on_checkpoint(session, task, exc.url)
            except Exception:
                logger.exception("Re-authentication failed for %s", task)
            # Either way, mark this task FAILED; reconcile will re-create a
            # fresh task for the deal on the next idle cycle.
            task.mark_failed()
            continue
        except ModelHTTPError as e:
            task.mark_failed()
            logger.error(
                colored("Daemon stopped — LLM API error", "red", attrs=["bold"])
                + "\n%s\nCheck llm_provider, ai_model, llm_api_key, and llm_api_base in Admin → Site Configuration.", e,
            )
            return
        except Exception:
            task.mark_failed()
            logger.exception("Task %s failed", task)
            continue

        task.mark_completed()
        # TODO(tmp): Cloud/CLI promo disabled — still advertises the retired
        # openoutreach CLI (GH issue). Re-enable with email-first messaging.
        # cloud_promo.maybe_log()
        rhythm.maybe_break()
