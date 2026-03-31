# linkedin/tasks/connect.py
"""Connect task — pulls one candidate, connects, self-reschedules.

Works for both regular and freemium campaigns via ConnectStrategy.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Callable

from django.utils import timezone
from termcolor import colored

from linkedin.conf import (
    ACTIVE_END_HOUR,
    ACTIVE_START_HOUR,
    CAMPAIGN_CONFIG,
    CONNECTION_NOTE_FALLBACK,
    CONNECTION_NOTE_PERSONALIZED,
    ENABLE_ACTIVE_HOURS,
)
from linkedin.db.deals import increment_connect_attempts, set_profile_state
from linkedin.db.leads import disqualify_lead
from linkedin.models import ActionLog, Task
from linkedin.enums import ProfileState
from linkedin.exceptions import ReachedConnectionLimit, SkipProfile

logger = logging.getLogger(__name__)

MAX_CONNECT_ATTEMPTS = 3


def build_connection_note(lead_id: int | None) -> str:
    """Build a personalized connection note from Lead data."""
    if not lead_id:
        return CONNECTION_NOTE_FALLBACK

    from crm.models import Lead

    lead = Lead.objects.filter(pk=lead_id).first()
    first_name = lead.first_name.strip() if lead and lead.first_name else ""

    if first_name:
        return CONNECTION_NOTE_PERSONALIZED.format(first_name=first_name)
    return CONNECTION_NOTE_FALLBACK


@dataclass
class ConnectStrategy:
    find_candidate: Callable
    pre_connect: Callable | None
    delay: float
    action_fraction: float  # 1.0 = always fire at base delay
    qualifier: object

    def compute_delay(self, elapsed: float) -> float:
        """Delay until next connect, scaled by elapsed execution time for freemium campaigns."""
        if self.action_fraction >= 1.0:
            return self.delay
        return max(self.delay, elapsed * (1 - self.action_fraction) / self.action_fraction)


def strategy_for(campaign, qualifiers):
    """Build the right ConnectStrategy based on campaign type."""
    qualifier = qualifiers.get(campaign.pk)

    if campaign.is_freemium:
        from linkedin.db.deals import create_freemium_deal
        from linkedin.pipeline.freemium_pool import find_freemium_candidate

        fraction = campaign.action_fraction
        return ConnectStrategy(
            find_candidate=lambda s: find_freemium_candidate(s, qualifier),
            pre_connect=lambda s, pid: create_freemium_deal(s, pid),
            delay=CAMPAIGN_CONFIG["connect_delay_seconds"],
            action_fraction=fraction,
            qualifier=qualifier,
        )

    from linkedin.pipeline.pools import find_candidate

    return ConnectStrategy(
        find_candidate=lambda s: find_candidate(s, qualifier),
        pre_connect=None,
        delay=CAMPAIGN_CONFIG["connect_delay_seconds"],
        action_fraction=1.0,
        qualifier=qualifier,
    )


def _seconds_until_tomorrow() -> float:
    from django.utils import timezone
    import datetime

    now = timezone.now()
    tomorrow = (now + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    return (tomorrow - now).total_seconds()


def recommended_action_delay(profile, action_type: str) -> float:
    """Spread actions across the active window instead of firing in bursts."""
    if action_type == ActionLog.ActionType.CONNECT:
        daily_limit = max(profile.connect_daily_limit or 1, 1)
    else:
        daily_limit = max(profile.follow_up_daily_limit or 1, 1)

    active_hours = ACTIVE_END_HOUR - ACTIVE_START_HOUR if ENABLE_ACTIVE_HOURS else 24
    window_seconds = max(active_hours, 1) * 3600
    base_delay = window_seconds / daily_limit
    return max(
        CAMPAIGN_CONFIG["min_action_interval"],
        random.uniform(base_delay * 0.7, base_delay * 1.3),
    )


def handle_connect(task, session, qualifiers):
    from linkedin.actions.connect import send_connection_request
    from linkedin.actions.status import get_connection_status

    cfg = CAMPAIGN_CONFIG
    campaign = session.campaign
    campaign_id = campaign.pk
    strategy = strategy_for(campaign, qualifiers)

    def _reschedule():
        elapsed = (timezone.now() - task.started_at).total_seconds() if task.started_at else 0
        enqueue_connect(
            campaign_id,
            delay_seconds=max(
                strategy.compute_delay(elapsed),
                recommended_action_delay(session.linkedin_profile, ActionLog.ActionType.CONNECT),
            ),
        )

    # --- Rate limit check ---
    if not session.linkedin_profile.can_execute(ActionLog.ActionType.CONNECT):
        enqueue_connect(campaign_id, delay_seconds=_seconds_until_tomorrow())
        return

    # --- Get candidate ---
    candidate = strategy.find_candidate(session)
    if candidate is None:
        enqueue_connect(campaign_id, delay_seconds=cfg["connect_no_candidate_delay_seconds"])
        return

    public_id = candidate["public_identifier"]
    profile = candidate.get("profile") or candidate

    # Freemium campaigns need a Deal before set_profile_state
    if strategy.pre_connect:
        strategy.pre_connect(session, public_id)

    from linkedin.db.urls import public_id_to_url
    from crm.models import Deal

    deal = Deal.objects.filter(
        lead__linkedin_url=public_id_to_url(public_id),
        campaign=session.campaign,
    ).first()
    reason = deal.reason if deal else ""
    stats = strategy.qualifier.explain(candidate, session) if strategy.qualifier else ""
    logger.info("[%s] %s", campaign, colored("\u25b6 connect", "cyan", attrs=["bold"]))
    logger.info("[%s] %s (%s) — %s", campaign, public_id, stats, reason or "")

    try:
        status = get_connection_status(session, profile)

        if status == ProfileState.CONNECTED:
            set_profile_state(session, public_id, status.value)
            enqueue_follow_up(
                campaign_id,
                public_id,
                delay_seconds=recommended_action_delay(
                    session.linkedin_profile, ActionLog.ActionType.FOLLOW_UP,
                ),
            )
            _reschedule()
            return

        if status == ProfileState.PENDING:
            set_profile_state(session, public_id, status.value)
            enqueue_check_pending(
                campaign_id, public_id,
                backoff_hours=cfg["check_pending_recheck_after_hours"],
            )
            _reschedule()
            return

        note = build_connection_note(candidate.get("lead_id"))
        new_state = send_connection_request(session=session, profile=profile, note=note)

        if new_state == ProfileState.QUALIFIED:
            # No Connect button found — track attempt, disqualify after MAX_CONNECT_ATTEMPTS
            attempts = increment_connect_attempts(session, public_id)
            if attempts >= MAX_CONNECT_ATTEMPTS:
                reason = f"Unreachable: no Connect button after {attempts} attempts"
                disqualify_lead(public_id)
                set_profile_state(session, public_id, ProfileState.FAILED.value, reason=reason)
                logger.warning("Disqualified %s — %s", public_id, reason)
            else:
                set_profile_state(session, public_id, new_state.value)
                logger.debug("%s: connect attempt %d/%d — no button found", public_id, attempts, MAX_CONNECT_ATTEMPTS)
        else:
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
                enqueue_follow_up(
                    campaign_id,
                    public_id,
                    delay_seconds=recommended_action_delay(
                        session.linkedin_profile, ActionLog.ActionType.FOLLOW_UP,
                    ),
                )

    except ReachedConnectionLimit as e:
        logger.warning("Rate limited: %s", e)
        session.linkedin_profile.mark_exhausted(ActionLog.ActionType.CONNECT)
        enqueue_connect(campaign_id, delay_seconds=_seconds_until_tomorrow())
        return
    except SkipProfile as e:
        logger.warning("Skipping %s: %s", public_id, e)
        set_profile_state(session, public_id, ProfileState.FAILED.value)

    _reschedule()


# ------------------------------------------------------------------
# Enqueue helpers (used by all task types)
# ------------------------------------------------------------------

def _enqueue_task(task_type: "Task.TaskType", payload: dict, delay_seconds: float, dedup_keys: list[str] | None = None):
    """Create a pending task if no duplicate exists.

    Deduplication: matches on task_type + status=PENDING + dedup_keys payload
    fields (defaults to all payload keys).
    """
    from datetime import timedelta

    filter_kwargs = {
        "task_type": task_type,
        "status": Task.Status.PENDING,
    }
    for key in (dedup_keys if dedup_keys is not None else payload):
        filter_kwargs[f"payload__{key}"] = payload[key]

    if not Task.objects.filter(**filter_kwargs).exists():
        Task.objects.create(
            task_type=task_type,
            scheduled_at=timezone.now() + timedelta(seconds=delay_seconds),
            payload=payload,
        )


def enqueue_connect(campaign_id: int, delay_seconds: float = 10):
    _enqueue_task(
        task_type=Task.TaskType.CONNECT,
        payload={"campaign_id": campaign_id},
        delay_seconds=delay_seconds,
    )


def enqueue_check_pending(
    campaign_id: int,
    public_id: str,
    backoff_hours: float,
    jitter_factor: float | None = None,
):
    if jitter_factor is None:
        jitter_factor = CAMPAIGN_CONFIG["check_pending_jitter_factor"]

    delay_hours = backoff_hours * random.uniform(1.0, 1.0 + jitter_factor)

    _enqueue_task(
        task_type=Task.TaskType.CHECK_PENDING,
        payload={
            "campaign_id": campaign_id,
            "public_id": public_id,
            "backoff_hours": backoff_hours,
        },
        delay_seconds=delay_hours * 3600,
        dedup_keys=["campaign_id", "public_id"],
    )


def enqueue_follow_up(campaign_id: int, public_id: str, delay_seconds: float = 10):
    _enqueue_task(
        task_type=Task.TaskType.FOLLOW_UP,
        payload={"campaign_id": campaign_id, "public_id": public_id},
        delay_seconds=delay_seconds,
    )
