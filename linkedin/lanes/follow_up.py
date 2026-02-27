# linkedin/lanes/follow_up.py
from __future__ import annotations

import logging

from termcolor import colored

from linkedin.conf import PARTNER_LOG_LEVEL
from linkedin.db.crm_profiles import get_connected_profiles, set_profile_state, save_chat_message
from linkedin.navigation.enums import ProfileState
from linkedin.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class FollowUpLane:
    def __init__(self, session, rate_limiter: RateLimiter):
        self.session = session
        self.rate_limiter = rate_limiter

    @property
    def _is_partner(self):
        return getattr(self.session.campaign, "is_partner", False)

    @property
    def _log_level(self):
        return PARTNER_LOG_LEVEL if self._is_partner else logging.INFO

    def can_execute(self) -> bool:
        return (
            self.rate_limiter.can_execute()
            and len(get_connected_profiles(self.session)) > 0
        )

    def execute(self) -> str | None:
        """Send a follow-up message to the first connected profile.

        Returns the ``public_id`` of the profile messaged, or ``None`` if
        there was nothing to do or the message failed.
        """
        tag = "[Partner] " if self._is_partner else ""
        logger.log(self._log_level, "%s%s", tag, colored("▶ follow_up", "green", attrs=["bold"]))
        from linkedin.actions.message import send_follow_up_message

        profiles = get_connected_profiles(self.session)
        if not profiles:
            return None

        candidate = profiles[0]
        public_id = candidate["public_identifier"]
        profile = candidate.get("profile") or candidate

        message_text = send_follow_up_message(
            session=self.session,
            profile=profile,
        )

        if message_text is not None:
            try:
                save_chat_message(self.session, public_id, message_text)
            finally:
                # Guarantee these once the message is sent,
                # even if chat save crashes.
                self.rate_limiter.record()
                set_profile_state(self.session, public_id, ProfileState.COMPLETED.value)
            return public_id

        return None
