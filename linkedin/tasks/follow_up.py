# linkedin/tasks/follow_up.py
"""Follow-up task — sends a message to one CONNECTED profile."""
from __future__ import annotations

import logging

from termcolor import colored

from linkedin.conf import PARTNER_LOG_LEVEL
from linkedin.db.crm_profiles import (
    get_profile_dict_for_public_id,
    save_chat_message,
    set_profile_state,
)
from linkedin.models import ActionLog
from linkedin.navigation.enums import ProfileState

logger = logging.getLogger(__name__)


def handle_follow_up(task, session, qualifiers, partner_qualifier, kit_model):
    from linkedin.actions.message import send_follow_up_message
    from linkedin.tasks.connect import enqueue_follow_up

    payload = task.payload
    public_id = payload["public_id"]
    campaign_id = payload["campaign_id"]

    is_partner = getattr(session.campaign, "is_partner", False)
    log_level = PARTNER_LOG_LEVEL if is_partner else logging.INFO
    tag = "[Partner] " if is_partner else ""
    logger.log(
        log_level, "%s%s %s",
        tag, colored("\u25b6 follow_up", "green", attrs=["bold"]), public_id,
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

    message_text = send_follow_up_message(
        session=session,
        profile=profile,
    )

    if message_text is not None:
        try:
            save_chat_message(session, public_id, message_text)
        finally:
            session.linkedin_profile.record_action(
                ActionLog.ActionType.FOLLOW_UP, session.campaign,
            )
            set_profile_state(session, public_id, ProfileState.COMPLETED.value)
