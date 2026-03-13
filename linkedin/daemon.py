# linkedin/daemon.py
from __future__ import annotations

import logging
import random
import time
import traceback

from django.utils import timezone
from termcolor import colored

from linkedin.conf import CAMPAIGN_CONFIG, _LEGACY_MODEL_PATH, model_path_for_campaign
from linkedin.diagnostics import failure_diagnostics
from linkedin.ml.qualifier import BayesianQualifier, KitQualifier
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


class _FreemiumRotator:
    """Logs rotating freemium messages every *every* task executions."""

    _MESSAGES = [
        colored("Join the community or give direct feedback on Telegram \u2192 https://t.me/+Y5bh9Vg8UVg5ODU0", "blue", attrs=["bold"]),
        "\033[38;5;208;1mLove OpenOutreach? Sponsor the project \u2192 https://github.com/sponsors/eracle\033[0m",
    ]

    def __init__(self, every: int = 10):
        self._every = every
        self._ticks = 0
        self._next = 0

    def maybe_log(self):
        self._ticks += 1
        if self._ticks % self._every == 0:
            logger.info(self._MESSAGES[self._next % len(self._MESSAGES)])
            self._next += 1


def _migrate_legacy_model(campaigns):
    """Migrate old global model.joblib to per-campaign path if possible."""
    if not _LEGACY_MODEL_PATH.exists():
        return

    non_freemium = [c for c in campaigns if not c.is_freemium]
    if len(non_freemium) == 1:
        dest = model_path_for_campaign(non_freemium[0].pk)
        if dest.exists():
            logger.info("Legacy model.joblib exists but %s already present — skipping migration", dest.name)
            return
        _LEGACY_MODEL_PATH.rename(dest)
        logger.info("Migrated legacy model.joblib → %s", dest.name)
    else:
        logger.warning(
            "Legacy model.joblib found but %d non-freemium campaigns exist — "
            "cannot auto-migrate. Remove it manually once per-campaign models are trained.",
            len(non_freemium),
        )


def _build_qualifiers(campaigns, cfg, kit_model=None):
    """Create a qualifier for every campaign, keyed by campaign PK.

    Regular campaigns get a ``BayesianQualifier`` (persisted per-campaign).
    Freemium campaigns get a ``KitQualifier`` backed by the pre-trained kit
    model directly — no inner BayesianQualifier needed.
    """
    from linkedin.models import ProfileEmbedding

    X, y = ProfileEmbedding.get_labeled_arrays()

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
                save_path=model_path_for_campaign(campaign.pk),
            )
            if len(X) > 0:
                q.warm_start(X, y)
            qualifiers[campaign.pk] = q
            n_regular += 1

    if n_regular and len(X) > 0:
        logger.info(
            colored("GP qualifiers warm-started", "cyan")
            + " on %d labelled samples (%d positive, %d negative)"
            + " for %d campaign(s)",
            len(y), int((y == 1).sum()), int((y == 0).sum()), n_regular,
        )

    return qualifiers


# ------------------------------------------------------------------
# Task queue worker
# ------------------------------------------------------------------


def _pop_next_task() -> Task | None:
    """Claim the oldest due pending task. Returns None if queue is empty."""
    now = timezone.now()
    # Future PostgreSQL: add .select_for_update(skip_locked=True) before .first()
    return (
        Task.objects.filter(status=Task.Status.PENDING, scheduled_at__lte=now)
        .order_by("scheduled_at")
        .first()
    )


def heal_tasks(session):
    """Reconcile task queue with CRM state on daemon startup.

    1. Reset stale 'running' tasks to 'pending' (crashed worker recovery)
    2. Seed one 'connect' task per campaign if none pending
    3. Create 'check_pending' tasks for PENDING profiles without tasks
    4. Create 'follow_up' tasks for CONNECTED profiles without tasks
    """
    from crm.models import Deal, Stage
    from linkedin.db.deals import parse_next_step
    from linkedin.db.urls import url_to_public_id
    from linkedin.enums import ProfileState

    cfg = CAMPAIGN_CONFIG

    # 1. Recover stale running tasks
    stale_count = Task.objects.filter(status=Task.Status.RUNNING).update(
        status=Task.Status.PENDING,
    )
    if stale_count:
        logger.info("Recovered %d stale running tasks", stale_count)

    # 2. Seed connect tasks per campaign (regular first, freemium deferred)
    for campaign in session.campaigns:
        delay = CAMPAIGN_CONFIG["connect_delay_seconds"] if campaign.is_freemium else 0
        enqueue_connect(campaign.pk, delay_seconds=delay)

    # 3. Check_pending tasks for PENDING profiles
    for campaign in session.campaigns:
        session.campaign = campaign
        stage = Stage.objects.filter(
            name=ProfileState.PENDING.value,
            department=campaign.department,
        ).first()
        if not stage:
            continue

        pending_deals = Deal.objects.filter(
            stage=stage,
            owner=session.django_user,
        ).select_related("lead")

        for deal in pending_deals:
            public_id = url_to_public_id(deal.lead.website) if deal.lead.website else None
            if not public_id:
                continue
            meta = parse_next_step(deal)
            backoff = meta.get("backoff_hours", cfg["check_pending_recheck_after_hours"])
            enqueue_check_pending(campaign.pk, public_id, backoff_hours=backoff)

    # 4. Follow_up tasks for CONNECTED profiles
    for campaign in session.campaigns:
        session.campaign = campaign
        stage = Stage.objects.filter(
            name=ProfileState.CONNECTED.value,
            department=campaign.department,
        ).first()
        if not stage:
            continue

        connected_deals = Deal.objects.filter(
            stage=stage,
            owner=session.django_user,
        ).select_related("lead")

        for deal in connected_deals:
            public_id = url_to_public_id(deal.lead.website) if deal.lead.website else None
            if not public_id:
                continue
            enqueue_follow_up(campaign.pk, public_id, delay_seconds=random.uniform(5, 60))

    pending_count = Task.objects.filter(status=Task.Status.PENDING).count()
    logger.info("Task queue healed: %d pending tasks", pending_count)


def run_daemon(session):
    from linkedin.management.setup_crm import ensure_campaign_pipeline
    from linkedin.ml.hub import fetch_kit
    from linkedin.setup.freemium import import_freemium_campaign
    from linkedin.models import Campaign

    cfg = CAMPAIGN_CONFIG

    # Load kit model for freemium campaigns
    kit = fetch_kit()
    if kit:
        import_freemium_campaign(kit["config"])
        from linkedin.setup.freemium import seed_profiles
        seed_profiles(session, kit["config"])

    # Migrate legacy single model file before creating per-campaign qualifiers
    _migrate_legacy_model(list(session.campaigns))

    qualifiers = _build_qualifiers(
        session.campaigns, cfg, kit_model=kit["model"] if kit else None,
    )

    # Ensure pipeline stages exist for all campaigns
    for campaign in session.campaigns:
        session.campaign = campaign
        ensure_campaign_pipeline(campaign.department)

    # Startup healing
    heal_tasks(session)

    campaigns = list(session.campaigns)
    if not campaigns:
        logger.error("No campaigns found — cannot start daemon")
        return

    logger.info(
        colored("Daemon started", "green", attrs=["bold"])
        + " — %d campaigns, task queue worker",
        len(campaigns),
    )

    freemium = _FreemiumRotator(every=2)

    while True:
        task = _pop_next_task()
        if task is None:
            time.sleep(cfg["worker_poll_seconds"])
            continue

        campaign = Campaign.objects.filter(pk=task.payload.get("campaign_id")).first()
        if not campaign:
            task.status = Task.Status.FAILED
            task.error = f"Campaign {task.payload.get('campaign_id')} not found"
            task.save(update_fields=["status", "error"])
            continue

        session.campaign = campaign

        task.status = Task.Status.RUNNING
        task.started_at = timezone.now()
        task.save(update_fields=["status", "started_at"])

        handler = _HANDLERS.get(task.task_type)
        if handler is None:
            task.status = Task.Status.FAILED
            task.error = f"Unknown task type: {task.task_type}"
            task.save(update_fields=["status", "error"])
            continue

        try:
            with failure_diagnostics(session):
                handler(task, session, qualifiers)
        except Exception:
            task.status = Task.Status.FAILED
            task.error = traceback.format_exc()
            task.save(update_fields=["status", "error"])
            logger.exception("Task %s failed", task)
            continue

        task.status = Task.Status.COMPLETED
        task.completed_at = timezone.now()
        task.save(update_fields=["status", "completed_at"])
        freemium.maybe_log()
