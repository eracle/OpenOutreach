# linkedin/lanes/connect.py
from __future__ import annotations

import logging

from linkedin.conf import CAMPAIGN_CONFIG
from linkedin.db.crm_profiles import (
    count_enriched_profiles,
    get_enriched_profiles,
    set_profile_state,
)
from linkedin.navigation.enums import ProfileState
from linkedin.navigation.exceptions import SkipProfile, ReachedConnectionLimit
from linkedin.rate_limiter import RateLimiter
from linkedin.ml.scorer import ProfileScorer

logger = logging.getLogger(__name__)


class ConnectLane:
    def __init__(self, session, rate_limiter: RateLimiter, scorer: ProfileScorer):
        self.session = session
        self.rate_limiter = rate_limiter
        self.scorer = scorer

    def can_execute(self) -> bool:
        return self.rate_limiter.can_execute() and count_enriched_profiles(self.session) > 0

    def execute(self):
        from linkedin.actions.connect import send_connection_request
        from linkedin.actions.connection_status import get_connection_status

        profiles = get_enriched_profiles(self.session)
        if not profiles:
            return

        ranked = self.scorer.score_profiles(profiles)
        candidate = ranked[0]

        public_id = candidate["public_identifier"]
        profile = candidate.get("profile") or candidate

        explanation = self.scorer.explain_profile(candidate)
        logger.debug("ML explanation for %s:\n%s", public_id, explanation)

        try:
            # Check actual connection status on the page before attempting to connect.
            # This catches pre-existing connections that slipped through enrich
            # (connection_degree was None at scrape time).
            connection_status = get_connection_status(self.session, profile)

            if connection_status == ProfileState.CONNECTED:
                if CAMPAIGN_CONFIG["follow_up_existing_connections"]:
                    set_profile_state(self.session, public_id, ProfileState.CONNECTED.value)
                else:
                    set_profile_state(
                        self.session, public_id, ProfileState.IGNORED.value,
                        reason="Pre-existing connection detected during connect (degree was unknown at scrape time)",
                    )
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
