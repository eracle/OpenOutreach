# linkedin/lanes/connect.py
"""Connect lane — thin wrapper around pool management.

execute() calls get_candidate() from pools, then connects.
"""
from __future__ import annotations

import logging

from termcolor import colored

from linkedin.conf import CAMPAIGN_CONFIG, PARTNER_LOG_LEVEL
from linkedin.db.crm_profiles import set_profile_state
from linkedin.ml.qualifier import BayesianQualifier
from linkedin.models import ActionLog
from linkedin.navigation.enums import ProfileState
from linkedin.navigation.exceptions import SkipProfile, ReachedConnectionLimit
from linkedin.pipeline.pools import get_candidate

logger = logging.getLogger(__name__)


class ConnectLane:
    def __init__(self, session, qualifier: BayesianQualifier, pipeline=None):
        self.session = session
        self.qualifier = qualifier
        self.pipeline = pipeline
        self._cfg = CAMPAIGN_CONFIG

    @property
    def _is_partner(self):
        return getattr(self.session.campaign, "is_partner", False)

    @property
    def _log_level(self):
        return PARTNER_LOG_LEVEL if self._is_partner else logging.INFO

    def can_execute(self) -> bool:
        return self.session.linkedin_profile.can_execute(ActionLog.ActionType.CONNECT)

    def execute(self) -> str | None:
        candidate = get_candidate(
            self.session, self.qualifier,
            min_prob=self._cfg["min_connect_prob"],
            pipeline=self.pipeline,
            is_partner=self._is_partner,
        )
        if candidate is None:
            return None

        tag = "[Partner] " if self._is_partner else ""
        logger.log(self._log_level, "%s%s", tag, colored("\u25b6 connect", "cyan", attrs=["bold"]))
        return self._connect(candidate)

    # ------------------------------------------------------------------
    # Connect action
    # ------------------------------------------------------------------

    def _connect(self, candidate: dict) -> str | None:
        from linkedin.actions.connect import send_connection_request
        from linkedin.actions.connection_status import get_connection_status
        from linkedin.models import ProfileEmbedding

        public_id = candidate["public_identifier"]
        profile = candidate.get("profile") or candidate

        reason = ProfileEmbedding.objects.filter(
            public_identifier=public_id, label__isnull=False,
        ).values_list("llm_reason", flat=True).first()
        stats = self.qualifier.explain(candidate)
        tag = "[Partner] " if self._is_partner else ""
        logger.log(self._log_level, "%s%s (%s) — %s", tag, public_id, stats, reason or "")

        try:
            status = get_connection_status(self.session, profile)

            if status in (ProfileState.CONNECTED, ProfileState.PENDING):
                set_profile_state(self.session, public_id, status.value)
                return public_id

            new_state = send_connection_request(session=self.session, profile=profile)
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
