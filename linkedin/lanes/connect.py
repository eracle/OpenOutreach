# linkedin/lanes/connect.py
from __future__ import annotations

import logging

from termcolor import colored

from linkedin.conf import PARTNER_LOG_LEVEL
from linkedin.db.crm_profiles import (
    count_qualified_profiles,
    get_qualified_profiles,
    set_profile_state,
)
from linkedin.models import ActionLog
from linkedin.navigation.enums import ProfileState
from linkedin.navigation.exceptions import SkipProfile, ReachedConnectionLimit
from linkedin.ml.qualifier import BayesianQualifier

logger = logging.getLogger(__name__)


class ConnectLane:
    def __init__(self, session, qualifier: BayesianQualifier, pipeline=None):
        self.session = session
        self.qualifier = qualifier
        self.pipeline = pipeline

    @property
    def _is_partner(self):
        return getattr(self.session.campaign, "is_partner", False)

    @property
    def _log_level(self):
        return PARTNER_LOG_LEVEL if self._is_partner else logging.INFO

    def can_execute(self) -> bool:
        return (
            self.session.linkedin_profile.can_execute(ActionLog.ActionType.CONNECT)
            and count_qualified_profiles(self.session) > 0
        )

    def execute(self) -> str | None:
        """Connect to the top-ranked qualified profile.

        Returns the ``public_id`` of the profile processed, or ``None`` if
        there was nothing to do.
        """
        tag = "[Partner] " if self._is_partner else ""
        logger.log(self._log_level, "%s%s", tag, colored("▶ connect", "cyan", attrs=["bold"]))
        from linkedin.actions.connect import send_connection_request
        from linkedin.actions.connection_status import get_connection_status

        profiles = get_qualified_profiles(self.session)
        if not profiles:
            return None

        ranked = self.qualifier.rank_profiles(profiles, pipeline=self.pipeline)
        candidate = ranked[0]

        public_id = candidate["public_identifier"]
        profile = candidate.get("profile") or candidate

        from linkedin.ml.embeddings import get_qualification_reason
        reason = get_qualification_reason(public_id)
        stats = self.qualifier.explain_profile(candidate)
        tag = "[Partner] " if self._is_partner else ""
        logger.log(self._log_level, "%s%s (%s) — %s", tag, public_id, stats, reason or "")

        try:
            # Check actual connection status on the page before attempting to connect.
            connection_status = get_connection_status(self.session, profile)

            if connection_status == ProfileState.CONNECTED:
                set_profile_state(self.session, public_id, ProfileState.CONNECTED.value)
                return public_id

            if connection_status == ProfileState.PENDING:
                set_profile_state(self.session, public_id, ProfileState.PENDING.value)
                return public_id

            new_state = send_connection_request(
                session=self.session,
                profile=profile,
            )
            set_profile_state(self.session, public_id, new_state.value)
            self.session.linkedin_profile.record_action(
                ActionLog.ActionType.CONNECT, self.session.campaign,
            )

        except ReachedConnectionLimit as e:
            logger.warning("Rate limited: %s", e)
            self.session.linkedin_profile.mark_exhausted(ActionLog.ActionType.CONNECT)
        except SkipProfile as e:
            logger.warning("Skipping %s: %s", public_id, e)
            set_profile_state(self.session, public_id, ProfileState.FAILED.value)

        return public_id
