# linkedin/tasks/connect.py
"""Connect task — pulls one candidate from pools, connects, self-reschedules."""
from __future__ import annotations

import logging
import random

from termcolor import colored

from linkedin.conf import CAMPAIGN_CONFIG, PARTNER_LOG_LEVEL
from linkedin.db.crm_profiles import seed_partner_deals, set_profile_state
from linkedin.models import ActionLog
from linkedin.navigation.enums import ProfileState
from linkedin.navigation.exceptions import ReachedConnectionLimit, SkipProfile
from linkedin.pipeline.pools import get_candidate

logger = logging.getLogger(__name__)


def _seconds_until_tomorrow() -> float:
    from django.utils import timezone
    import datetime

    now = timezone.now()
    tomorrow = (now + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    return (tomorrow - now).total_seconds()


def handle_connect(task, session, qualifiers, partner_qualifier, kit_model):
    from linkedin.actions.connect import send_connection_request
    from linkedin.actions.connection_status import get_connection_status
    from linkedin.models import ProfileEmbedding

    cfg = CAMPAIGN_CONFIG
    campaign = session.campaign
    campaign_id = campaign.pk
    is_partner = getattr(campaign, "is_partner", False)
    log_level = PARTNER_LOG_LEVEL if is_partner else logging.INFO

    # --- Probabilistic gating ---
    partner_fraction = max(
        (c.action_fraction for c in session.campaigns if c.is_partner),
        default=0.0,
    )
    if is_partner:
        if random.random() >= campaign.action_fraction:
            enqueue_connect(campaign_id, delay_seconds=cfg["connect_delay_seconds"])
            return
        seed_partner_deals(session)
        qualifier = partner_qualifier
        pipeline = kit_model
    else:
        if partner_fraction > 0 and random.random() < partner_fraction:
            enqueue_connect(campaign_id, delay_seconds=cfg["connect_delay_seconds"])
            return
        qualifier = qualifiers.get(campaign_id)
        pipeline = None

    # --- Rate limit check ---
    if not session.linkedin_profile.can_execute(ActionLog.ActionType.CONNECT):
        enqueue_connect(campaign_id, delay_seconds=_seconds_until_tomorrow())
        return

    # --- Get candidate ---
    candidate = get_candidate(session, qualifier, pipeline=pipeline)
    if candidate is None:
        enqueue_connect(campaign_id, delay_seconds=cfg["connect_no_candidate_delay_seconds"])
        return

    public_id = candidate["public_identifier"]
    profile = candidate.get("profile") or candidate

    reason = (
        ProfileEmbedding.objects.filter(
            public_identifier=public_id, label__isnull=False,
        )
        .values_list("llm_reason", flat=True)
        .first()
    )
    stats = qualifier.explain(candidate, session) if qualifier else ""
    tag = "[Partner] " if is_partner else ""
    logger.log(log_level, "%s%s", tag, colored("\u25b6 connect", "cyan", attrs=["bold"]))
    logger.log(log_level, "%s%s (%s) — %s", tag, public_id, stats, reason or "")

    try:
        status = get_connection_status(session, profile)

        if status == ProfileState.CONNECTED:
            set_profile_state(session, public_id, status.value)
            enqueue_follow_up(campaign_id, public_id)
            enqueue_connect(campaign_id, delay_seconds=cfg["connect_delay_seconds"])
            return

        if status == ProfileState.PENDING:
            set_profile_state(session, public_id, status.value)
            enqueue_check_pending(
                campaign_id, public_id,
                backoff_hours=cfg["check_pending_recheck_after_hours"],
            )
            enqueue_connect(campaign_id, delay_seconds=cfg["connect_delay_seconds"])
            return

        new_state = send_connection_request(session=session, profile=profile)
        set_profile_state(session, public_id, new_state.value)
        session.linkedin_profile.record_action(
            ActionLog.ActionType.CONNECT, session.campaign,
        )

        if new_state == ProfileState.PENDING:
            enqueue_check_pending(
                campaign_id, public_id,
                backoff_hours=cfg["check_pending_recheck_after_hours"],
            )
        elif new_state == ProfileState.CONNECTED:
            enqueue_follow_up(campaign_id, public_id)

    except ReachedConnectionLimit as e:
        logger.warning("Rate limited: %s", e)
        session.linkedin_profile.mark_exhausted(ActionLog.ActionType.CONNECT)
        enqueue_connect(campaign_id, delay_seconds=_seconds_until_tomorrow())
        return
    except SkipProfile as e:
        logger.warning("Skipping %s: %s", public_id, e)
        set_profile_state(session, public_id, ProfileState.FAILED.value)

    enqueue_connect(campaign_id, delay_seconds=cfg["connect_delay_seconds"])


# ------------------------------------------------------------------
# Enqueue helpers (used by all task types)
# ------------------------------------------------------------------

def enqueue_connect(campaign_id: int, delay_seconds: float = 10):
    from datetime import timedelta
    from django.utils import timezone
    from linkedin.models import Task

    if not Task.objects.filter(
        task_type=Task.TaskType.CONNECT,
        status=Task.Status.PENDING,
        payload__campaign_id=campaign_id,
    ).exists():
        Task.objects.create(
            task_type=Task.TaskType.CONNECT,
            scheduled_at=timezone.now() + timedelta(seconds=delay_seconds),
            payload={"campaign_id": campaign_id},
        )


def enqueue_check_pending(
    campaign_id: int,
    public_id: str,
    backoff_hours: float,
    jitter_factor: float | None = None,
):
    from datetime import timedelta
    from django.utils import timezone
    from linkedin.models import Task

    if jitter_factor is None:
        jitter_factor = CAMPAIGN_CONFIG["check_pending_jitter_factor"]

    delay_hours = backoff_hours * random.uniform(1.0, 1.0 + jitter_factor)

    if not Task.objects.filter(
        task_type=Task.TaskType.CHECK_PENDING,
        status=Task.Status.PENDING,
        payload__public_id=public_id,
    ).exists():
        Task.objects.create(
            task_type=Task.TaskType.CHECK_PENDING,
            scheduled_at=timezone.now() + timedelta(hours=delay_hours),
            payload={
                "campaign_id": campaign_id,
                "public_id": public_id,
                "backoff_hours": backoff_hours,
            },
        )


def enqueue_follow_up(campaign_id: int, public_id: str, delay_seconds: float = 10):
    from datetime import timedelta
    from django.utils import timezone
    from linkedin.models import Task

    if not Task.objects.filter(
        task_type=Task.TaskType.FOLLOW_UP,
        status=Task.Status.PENDING,
        payload__public_id=public_id,
    ).exists():
        Task.objects.create(
            task_type=Task.TaskType.FOLLOW_UP,
            scheduled_at=timezone.now() + timedelta(seconds=delay_seconds),
            payload={"campaign_id": campaign_id, "public_id": public_id},
        )
