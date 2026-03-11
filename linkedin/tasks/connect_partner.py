# linkedin/tasks/connect_partner.py
"""Partner connect task — pulls candidate from partner pools, connects, self-reschedules."""
from __future__ import annotations

import logging

from termcolor import colored

from linkedin.conf import CAMPAIGN_CONFIG
from linkedin.db.crm_profiles import seed_partner_deals, set_profile_state
from linkedin.models import ActionLog
from linkedin.navigation.enums import ProfileState
from linkedin.navigation.exceptions import ReachedConnectionLimit, SkipProfile
from linkedin.pipeline.pools import get_candidate

logger = logging.getLogger(__name__)


def _partner_delay(campaign) -> float:
    """Compute partner reschedule delay from action_fraction.

    action_fraction acts as a ratio: delay = base_delay / fraction.
    E.g. fraction=0.2 → 10s / 0.2 = 50s, so 1 partner connect per 5 regular.
    """
    cfg = CAMPAIGN_CONFIG
    fraction = getattr(campaign, "action_fraction", 0.0) or 0.2
    return cfg["connect_delay_seconds"] / fraction


def _seconds_until_tomorrow() -> float:
    from django.utils import timezone
    import datetime

    now = timezone.now()
    tomorrow = (now + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    return (tomorrow - now).total_seconds()


def enqueue_connect_partner(campaign_id: int, delay_seconds: float = 10):
    from datetime import timedelta
    from django.utils import timezone
    from linkedin.models import Task

    if not Task.objects.filter(
        task_type=Task.TaskType.CONNECT_PARTNER,
        status=Task.Status.PENDING,
        payload__campaign_id=campaign_id,
    ).exists():
        Task.objects.create(
            task_type=Task.TaskType.CONNECT_PARTNER,
            scheduled_at=timezone.now() + timedelta(seconds=delay_seconds),
            payload={"campaign_id": campaign_id},
        )


def handle_connect_partner(task, session, qualifiers, partner_qualifier, kit_model):
    from linkedin.actions.connect import send_connection_request
    from linkedin.actions.connection_status import get_connection_status
    from linkedin.models import ProfileEmbedding
    from linkedin.tasks.connect import enqueue_check_pending, enqueue_follow_up

    cfg = CAMPAIGN_CONFIG
    campaign = session.campaign
    campaign_id = campaign.pk
    delay = _partner_delay(campaign)

    seed_partner_deals(session)

    # --- Rate limit check ---
    if not session.linkedin_profile.can_execute(ActionLog.ActionType.CONNECT):
        enqueue_connect_partner(campaign_id, delay_seconds=_seconds_until_tomorrow())
        return

    # --- Get candidate ---
    candidate = get_candidate(session, partner_qualifier, pipeline=kit_model)
    if candidate is None:
        enqueue_connect_partner(campaign_id, delay_seconds=cfg["connect_no_candidate_delay_seconds"])
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
    stats = partner_qualifier.explain(candidate, session) if partner_qualifier else ""
    logger.info("[%s] %s", campaign, colored("\u25b6 connect", "cyan", attrs=["bold"]))
    logger.info("[%s] %s (%s) — %s", campaign, public_id, stats, reason or "")

    try:
        status = get_connection_status(session, profile)

        if status == ProfileState.CONNECTED:
            set_profile_state(session, public_id, status.value)
            enqueue_follow_up(campaign_id, public_id)
            enqueue_connect_partner(campaign_id, delay_seconds=delay)
            return

        if status == ProfileState.PENDING:
            set_profile_state(session, public_id, status.value)
            enqueue_check_pending(
                campaign_id, public_id,
                backoff_hours=cfg["check_pending_recheck_after_hours"],
            )
            enqueue_connect_partner(campaign_id, delay_seconds=delay)
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
        enqueue_connect_partner(campaign_id, delay_seconds=_seconds_until_tomorrow())
        return
    except SkipProfile as e:
        logger.warning("Skipping %s: %s", public_id, e)
        set_profile_state(session, public_id, ProfileState.FAILED.value)

    enqueue_connect_partner(campaign_id, delay_seconds=delay)
