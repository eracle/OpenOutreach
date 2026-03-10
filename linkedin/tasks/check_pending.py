# linkedin/tasks/check_pending.py
"""Check pending task — checks one PENDING profile, self-reschedules with backoff."""
from __future__ import annotations

import json
import logging

from termcolor import colored

from linkedin.conf import PARTNER_LOG_LEVEL
from linkedin.db.crm_profiles import (
    get_profile_dict_for_public_id,
    public_id_to_url,
    set_profile_state,
)
from linkedin.navigation.enums import ProfileState
from linkedin.navigation.exceptions import SkipProfile

logger = logging.getLogger(__name__)


def handle_check_pending(task, session, qualifiers, partner_qualifier, kit_model):
    from crm.models import Deal
    from linkedin.actions.connection_status import get_connection_status
    from linkedin.tasks.connect import enqueue_check_pending, enqueue_follow_up

    payload = task.payload
    public_id = payload["public_id"]
    campaign_id = payload["campaign_id"]
    backoff_hours = payload.get("backoff_hours", 24)

    is_partner = getattr(session.campaign, "is_partner", False)
    log_level = PARTNER_LOG_LEVEL if is_partner else logging.INFO
    tag = "[Partner] " if is_partner else ""
    logger.log(
        log_level, "%s%s %s",
        tag, colored("\u25b6 check_pending", "magenta", attrs=["bold"]), public_id,
    )

    profile_dict = get_profile_dict_for_public_id(session, public_id)
    if profile_dict is None:
        logger.warning("check_pending: no Deal for %s — skipping", public_id)
        return

    profile = profile_dict.get("profile") or profile_dict

    try:
        new_state = get_connection_status(session, profile)
    except SkipProfile as e:
        logger.warning("Skipping %s: %s", public_id, e)
        set_profile_state(session, public_id, ProfileState.FAILED.value)
        return

    set_profile_state(session, public_id, new_state.value)

    if new_state == ProfileState.CONNECTED:
        enqueue_follow_up(campaign_id, public_id)
    elif new_state == ProfileState.PENDING:
        new_backoff = backoff_hours * 2
        clean_url = public_id_to_url(public_id)
        Deal.objects.filter(
            lead__website=clean_url,
            owner=session.django_user,
        ).update(next_step=json.dumps({"backoff_hours": new_backoff}))
        logger.debug(
            "%s still pending — backoff %.1fh → %.1fh",
            public_id, backoff_hours, new_backoff,
        )
        enqueue_check_pending(campaign_id, public_id, backoff_hours=new_backoff)
