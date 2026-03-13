# linkedin/tasks/follow_up.py
"""Follow-up task — runs the agentic follow-up for one CONNECTED profile."""
from __future__ import annotations

import logging

from termcolor import colored

from linkedin.db.deals import get_profile_dict_for_public_id
from linkedin.models import ActionLog

logger = logging.getLogger(__name__)


def handle_follow_up(task, session, qualifiers):
    from linkedin.agents.follow_up import run_follow_up_agent
    from linkedin.tasks.connect import enqueue_follow_up

    payload = task.payload
    public_id = payload["public_id"]
    campaign_id = payload["campaign_id"]

    logger.info(
        "[%s] %s %s",
        session.campaign, colored("\u25b6 follow_up", "green", attrs=["bold"]), public_id,
    )

    # Rate limit check
    if not session.linkedin_profile.can_execute(ActionLog.ActionType.FOLLOW_UP):
        enqueue_follow_up(campaign_id, public_id, delay_seconds=3600)
        return

    profile_dict = get_profile_dict_for_public_id(session, public_id)
    if profile_dict is None:
        logger.warning("follow_up: no Deal for %s — skipping", public_id)
        return

    profile = profile_dict.get("profile") or profile_dict

    result = run_follow_up_agent(session, public_id, profile, campaign_id)

    # Record action if any message was sent
    sent_messages = [a for a in result["actions"] if a["tool"] == "send_message"]
    if sent_messages:
        session.linkedin_profile.record_action(
            ActionLog.ActionType.FOLLOW_UP, session.campaign,
        )

    # Safety net: if agent didn't schedule or complete, re-enqueue
    terminal_tools = {"mark_completed", "schedule_follow_up"}
    if not any(a["tool"] in terminal_tools for a in result["actions"]):
        logger.warning("follow_up agent for %s did not schedule or complete — re-enqueuing in 72h", public_id)
        enqueue_follow_up(campaign_id, public_id, delay_seconds=72 * 3600)

    # Log summary
    action_names = [a["tool"] for a in result["actions"]]
    logger.info("follow_up agent for %s: %s", public_id, ", ".join(action_names) or "no actions")
