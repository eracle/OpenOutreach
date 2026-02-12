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


def _rebuild_analytics():
    """Run dbt to rebuild the analytics DB."""
    import subprocess

    from linkedin.conf import ROOT_DIR

    analytics_dir = ROOT_DIR / "analytics"
    logger.info(colored("Rebuilding analytics (dbt run)...", "cyan", attrs=["bold"]))
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
        return True
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning("dbt run failed: %s", e)
        return False


def run_daemon(session):
    cfg = CAMPAIGN_CONFIG

    scorer = ProfileScorer(seed=42)
    try:
        trained = scorer.train()
    except Exception:
        # Schema mismatch — rebuild analytics and retry
        if _rebuild_analytics():
            trained = scorer.train()
        else:
            trained = False

    if trained:
        logger.info(colored("ML model loaded from existing analytics data", "green", attrs=["bold"]))
    elif scorer.has_keywords:
        logger.info(colored("No analytics data yet — using keyword heuristic for ranking", "yellow"))
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
