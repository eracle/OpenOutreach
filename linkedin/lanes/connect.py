# linkedin/lanes/connect.py
from __future__ import annotations

import logging

from termcolor import colored

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

        profiles = get_enriched_profiles(self.session)
        if not profiles:
            return

        ranked = self.scorer.score_profiles(profiles)
        candidate = ranked[0]

        public_id = candidate["public_identifier"]
        profile = candidate.get("profile") or candidate

        try:
            new_state = send_connection_request(
                handle=self.session.handle,
                profile=profile,
            )
            set_profile_state(self.session, public_id, new_state.value)
            self.rate_limiter.record()

            explanation = self.scorer.explain_profile(candidate)
            logger.info("ML explanation for %s:\n%s", public_id, explanation)

        except ReachedConnectionLimit as e:
            logger.info(colored(f"Rate limited: {e}", "red", attrs=["bold"]))
            self.rate_limiter.mark_daily_exhausted()
        except SkipProfile as e:
            logger.info(colored(f"Skipping {public_id}: {e}", "red", attrs=["bold"]))
            set_profile_state(self.session, public_id, ProfileState.FAILED.value)
