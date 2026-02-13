# linkedin/lanes/follow_up.py
from __future__ import annotations

import logging

from linkedin.db.crm_profiles import get_connected_profiles, set_profile_state
from linkedin.navigation.enums import ProfileState, MessageStatus
from linkedin.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_MESSAGE_STATUS_TO_STATE = {
    MessageStatus.SENT: ProfileState.COMPLETED,
    MessageStatus.SKIPPED: ProfileState.CONNECTED,
}


class FollowUpLane:
    def __init__(self, session, rate_limiter: RateLimiter, recheck_after_hours: float):
        self.session = session
        self.rate_limiter = rate_limiter
        self.recheck_after_hours = recheck_after_hours

    def can_execute(self) -> bool:
        return (
            self.rate_limiter.can_execute()
            and len(get_connected_profiles(self.session, self.recheck_after_hours)) > 0
        )

    def execute(self):
        from linkedin.actions.message import send_follow_up_message

        profiles = get_connected_profiles(self.session, self.recheck_after_hours)
        if not profiles:
            return

        candidate = profiles[0]
        public_id = candidate["public_identifier"]
        profile = candidate.get("profile") or candidate

        status = send_follow_up_message(
            handle=self.session.handle,
            profile=profile,
        )
        new_state = _MESSAGE_STATUS_TO_STATE.get(status, ProfileState.CONNECTED)
        set_profile_state(self.session, public_id, new_state.value)
        self.rate_limiter.record()
