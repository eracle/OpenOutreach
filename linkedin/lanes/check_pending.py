# linkedin/lanes/check_pending.py
from __future__ import annotations

import json
import logging

from termcolor import colored

from linkedin.conf import PARTNER_LOG_LEVEL
from linkedin.db.crm_profiles import get_pending_profiles, set_profile_state
from linkedin.navigation.enums import ProfileState
from linkedin.navigation.exceptions import SkipProfile

logger = logging.getLogger(__name__)


class CheckPendingLane:
    def __init__(self, session, recheck_after_hours: float):
        self.session = session
        self.recheck_after_hours = recheck_after_hours

    @property
    def _is_partner(self):
        return getattr(self.session.campaign, "is_partner", False)

    @property
    def _log_level(self):
        return PARTNER_LOG_LEVEL if self._is_partner else logging.INFO

    def can_execute(self) -> bool:
        return len(get_pending_profiles(self.session, self.recheck_after_hours)) > 0

    def execute(self):
        tag = "[Partner] " if self._is_partner else ""
        logger.log(self._log_level, "%s%s", tag, colored("▶ check_pending", "magenta", attrs=["bold"]))
        from crm.models import Deal
        from linkedin.actions.connection_status import get_connection_status
        from linkedin.db.crm_profiles import public_id_to_url

        profiles = get_pending_profiles(self.session, self.recheck_after_hours)
        if not profiles:
            return

        for candidate in profiles:
            public_id = candidate["public_identifier"]
            profile = candidate.get("profile") or candidate

            try:
                new_state = get_connection_status(self.session, profile)
            except SkipProfile as e:
                logger.warning("Skipping %s: %s", public_id, e)
                set_profile_state(self.session, public_id, ProfileState.FAILED.value)
                continue

            set_profile_state(self.session, public_id, new_state.value)

            if new_state == ProfileState.PENDING:
                # Double the backoff for next recheck
                current_backoff = candidate.get("meta", {}).get(
                    "backoff_hours", self.recheck_after_hours
                )
                new_backoff = current_backoff * 2
                clean_url = public_id_to_url(public_id)
                # Use .update() to avoid refreshing update_date (auto_now)
                Deal.objects.filter(
                    lead__website=clean_url,
                    owner=self.session.django_user,
                ).update(next_step=json.dumps({"backoff_hours": new_backoff}))
                logger.debug(
                    "%s still pending — backoff %.1fh → %.1fh",
                    public_id, current_backoff, new_backoff,
                )

