# linkedin/daemon.py
from __future__ import annotations

import logging
import time

from termcolor import colored

from linkedin.conf import CAMPAIGN_CONFIG
from linkedin.lanes.check_pending import CheckPendingLane
from linkedin.lanes.connect import ConnectLane
from linkedin.lanes.enrich import EnrichLane
from linkedin.lanes.follow_up import FollowUpLane
from linkedin.ml.scorer import ProfileScorer
from linkedin.rate_limiter import RateLimiter
from linkedin.sessions.account import human_delay

logger = logging.getLogger(__name__)

LANE_COLORS = {
    "enrich": "yellow",
    "connect": "cyan",
    "check_pending": "magenta",
    "follow_up": "green",
}


def run_daemon(session):
    cfg = CAMPAIGN_CONFIG

    scorer = ProfileScorer(seed=42)
    if scorer.train():
        logger.info(colored("ML model loaded from existing analytics data", "green", attrs=["bold"]))
    else:
        logger.info(colored("No analytics data yet — ML scoring disabled (FIFO ordering)", "yellow"))

    connect_limiter = RateLimiter(
        daily_limit=cfg["connect_daily_limit"],
        weekly_limit=cfg["connect_weekly_limit"],
    )
    follow_up_limiter = RateLimiter(
        daily_limit=cfg["follow_up_daily_limit"],
    )

    lanes = [
        EnrichLane(session),
        ConnectLane(session, connect_limiter, scorer),
        CheckPendingLane(session, cfg["check_pending_min_age_days"], scorer),
        FollowUpLane(session, follow_up_limiter, cfg["follow_up_min_age_days"]),
    ]

    idle_sleep = cfg["idle_sleep_minutes"] * 60
    lane_names = ["enrich", "connect", "check_pending", "follow_up"]

    logger.info(colored("Daemon started", "green", attrs=["bold"]) + " — round-robin across %d lanes", len(lanes))

    while True:
        any_executed = False

        for lane, name in zip(lanes, lane_names):
            if lane.can_execute():
                color = LANE_COLORS[name]
                logger.info(colored(f"▶ {name}", color, attrs=["bold"]))
                lane.execute()
                any_executed = True

        if not any_executed:
            logger.info(colored("All lanes idle", "yellow") + " — sleeping %d minutes", cfg["idle_sleep_minutes"])
            time.sleep(idle_sleep)
