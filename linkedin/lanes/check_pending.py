# linkedin/lanes/check_pending.py
from __future__ import annotations

import logging
import subprocess

from termcolor import colored

from linkedin.conf import ROOT_DIR
from linkedin.db.crm_profiles import get_pending_profiles, set_profile_state
from linkedin.navigation.enums import ProfileState
from linkedin.ml.scorer import ProfileScorer

logger = logging.getLogger(__name__)


class CheckPendingLane:
    def __init__(self, session, recheck_after_hours: float, scorer: ProfileScorer):
        self.session = session
        self.recheck_after_hours = recheck_after_hours
        self.scorer = scorer

    def can_execute(self) -> bool:
        return len(get_pending_profiles(self.session, self.recheck_after_hours)) > 0

    def execute(self):
        from linkedin.actions.connection_status import get_connection_status

        profiles = get_pending_profiles(self.session, self.recheck_after_hours)
        if not profiles:
            return

        any_flipped = False
        for candidate in profiles:
            public_id = candidate["public_identifier"]
            profile = candidate.get("profile") or candidate

            new_state = get_connection_status(self.session, profile)
            set_profile_state(self.session, public_id, new_state.value)

            if new_state == ProfileState.CONNECTED:
                any_flipped = True

        if any_flipped:
            self._retrain()

    def _retrain(self):
        analytics_dir = ROOT_DIR / "analytics"
        logger.info(colored("Connection flip detected — rebuilding analytics + retraining ML model", "cyan", attrs=["bold"]))
        try:
            subprocess.run(
                ["dbt", "run"],
                cwd=str(analytics_dir),
                timeout=120,
                capture_output=True,
                text=True,
                check=True,
            )
            logger.info(colored("dbt run completed", "green"))
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.warning("dbt run failed: %s", e)

        if self.scorer.train():
            logger.info(colored("ML model retrained", "green", attrs=["bold"]))
        else:
            logger.warning("ML model retraining returned False")
