# linkedin/lanes/follow_up.py
from __future__ import annotations

import logging

from linkedin.db.crm_profiles import get_connected_profiles, set_profile_state, save_chat_message
from linkedin.navigation.enums import ProfileState
from linkedin.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class FollowUpLane:
    def __init__(self, session, rate_limiter: RateLimiter):
        self.session = session
        self.rate_limiter = rate_limiter

    def can_execute(self) -> bool:
        return (
            self.rate_limiter.can_execute()
            and len(get_connected_profiles(self.session)) > 0
        )

    def execute(self):
        from linkedin.actions.message import send_follow_up_message

        profiles = get_connected_profiles(self.session)
        if not profiles:
            return

        candidate = profiles[0]
        public_id = candidate["public_identifier"]
        profile = candidate.get("profile") or candidate

        message_text = send_follow_up_message(
            handle=self.session.handle,
            profile=profile,
        )

        if message_text is not None:
            set_profile_state(self.session, public_id, ProfileState.COMPLETED.value)
            save_chat_message(self.session, public_id, message_text)
        else:
            set_profile_state(self.session, public_id, ProfileState.CONNECTED.value)

        self.rate_limiter.record()
