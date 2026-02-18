# linkedin/lanes/connect.py
from __future__ import annotations

import logging

from linkedin.db.crm_profiles import (
    count_qualified_profiles,
    get_qualified_profiles,
    set_profile_state,
)
from linkedin.navigation.enums import ProfileState
from linkedin.navigation.exceptions import SkipProfile, ReachedConnectionLimit
from linkedin.rate_limiter import RateLimiter
from linkedin.ml.qualifier import BayesianQualifier
from termcolor import colored

logger = logging.getLogger(__name__)


class ConnectLane:
    def __init__(self, session, rate_limiter: RateLimiter, qualifier: BayesianQualifier,
                 pipeline=None):
        self.session = session
        self.rate_limiter = rate_limiter
        self.qualifier = qualifier
        self.pipeline = pipeline

    @property
    def _log_level(self):
        return 5 if getattr(self.session.campaign, "is_promo", False) else logging.INFO

    def can_execute(self) -> bool:
        return self.rate_limiter.can_execute() and count_qualified_profiles(self.session) > 0

    def execute(self):
        logger.log(self._log_level, colored("▶ connect", "cyan", attrs=["bold"]))
        from linkedin.actions.connect import send_connection_request
        from linkedin.actions.connection_status import get_connection_status

        profiles = get_qualified_profiles(self.session)
        if not profiles:
            return

        ranked = self.qualifier.rank_profiles(profiles, pipeline=self.pipeline)
        candidate = ranked[0]

        public_id = candidate["public_identifier"]
        profile = candidate.get("profile") or candidate

        from linkedin.ml.embeddings import get_qualification_reason
        reason = get_qualification_reason(public_id)
        if reason:
            logger.log(self._log_level, "Qualify motivation for %s: \n%s", public_id, reason)

        explanation = self.qualifier.explain_profile(candidate)
        logger.debug("ML explanation for %s:\n%s", public_id, explanation)

        try:
            # Check actual connection status on the page before attempting to connect.
            connection_status = get_connection_status(self.session, profile)

            if connection_status == ProfileState.CONNECTED:
                set_profile_state(self.session, public_id, ProfileState.CONNECTED.value)
                return

            if connection_status == ProfileState.PENDING:
                set_profile_state(self.session, public_id, ProfileState.PENDING.value)
                return

            new_state = send_connection_request(
                session=self.session,
                profile=profile,
            )
            set_profile_state(self.session, public_id, new_state.value)
            self.rate_limiter.record()


        except ReachedConnectionLimit as e:
            logger.warning("Rate limited: %s", e)
            self.rate_limiter.mark_daily_exhausted()
        except SkipProfile as e:
            logger.warning("Skipping %s: %s", public_id, e)
            set_profile_state(self.session, public_id, ProfileState.FAILED.value)
