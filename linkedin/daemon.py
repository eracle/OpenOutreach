# linkedin/daemon.py
from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timedelta

from termcolor import colored

from linkedin.conf import CAMPAIGN_CONFIG
from linkedin.db.crm_profiles import count_pending_scrape
from linkedin.lanes.check_pending import CheckPendingLane
from linkedin.lanes.connect import ConnectLane
from linkedin.lanes.enrich import EnrichLane
from linkedin.lanes.follow_up import FollowUpLane
from linkedin.ml.scorer import ProfileScorer
from linkedin.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

LANE_COLORS = {
    "enrich": "yellow",
    "connect": "cyan",
    "check_pending": "magenta",
    "follow_up": "green",
}



class LaneSchedule:
    """Tracks when a major lane should next fire."""

    def __init__(self, name: str, lane, base_interval_seconds: float):
        self.name = name
        self.lane = lane
        self.base_interval = base_interval_seconds
        self.next_run = time.time()  # fire immediately on first pass

    def reschedule(self):
        jitter = random.uniform(0.8, 1.2)
        self.next_run = time.time() + self.base_interval * jitter


def _parse_time(s: str) -> tuple[int, int]:
    """Parse "HH:MM" → (hour, minute)."""
    h, m = s.split(":")
    return int(h), int(m)


def _in_working_hours(start: tuple[int, int], end: tuple[int, int]) -> bool:
    now = datetime.now()
    current = now.hour * 60 + now.minute
    return (start[0] * 60 + start[1]) <= current < (end[0] * 60 + end[1])


def _seconds_until_work_starts(start: tuple[int, int]) -> float:
    now = datetime.now()
    target = now.replace(hour=start[0], minute=start[1], second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


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

    enrich_lane = EnrichLane(session)
    connect_lane = ConnectLane(session, connect_limiter, scorer)
    check_pending_lane = CheckPendingLane(session, cfg["check_pending_recheck_after_hours"], scorer)
    follow_up_lane = FollowUpLane(session, follow_up_limiter)

    # Working hours
    wh_start = _parse_time(cfg["working_hours_start"])
    wh_end = _parse_time(cfg["working_hours_end"])
    active_minutes = (wh_end[0] * 60 + wh_end[1]) - (wh_start[0] * 60 + wh_start[1])

    # Compute intervals (seconds) from active window / daily limit
    connect_interval = (active_minutes * 60) / cfg["connect_daily_limit"]
    follow_up_interval = (active_minutes * 60) / cfg["follow_up_daily_limit"]
    check_pending_interval = cfg["check_pending_recheck_after_hours"] * 3600
    min_enrich_interval = cfg["enrich_min_interval"]

    schedules = [
        LaneSchedule("connect", connect_lane, connect_interval),
        LaneSchedule("follow_up", follow_up_lane, follow_up_interval),
        LaneSchedule("check_pending", check_pending_lane, check_pending_interval),
    ]

    logger.info(
        colored("Daemon started", "green", attrs=["bold"])
        + " — working hours %s–%s, connect every %.0fm, follow_up every %.0fm, check_pending every %.0fm",
        cfg["working_hours_start"],
        cfg["working_hours_end"],
        connect_interval / 60,
        follow_up_interval / 60,
        check_pending_interval / 60,
    )

    while True:
        # ── Working hours gate ──
        if not _in_working_hours(wh_start, wh_end):
            wait = _seconds_until_work_starts(wh_start)
            logger.info(
                colored("Outside working hours", "yellow")
                + " — sleeping until %02d:%02d (%.0f min)",
                wh_start[0], wh_start[1], wait / 60,
            )
            time.sleep(wait)
            continue

        # ── Find soonest major action ──
        now = time.time()
        next_schedule = min(schedules, key=lambda s: s.next_run)
        gap = max(next_schedule.next_run - now, 0)

        # ── Fill gap with enrichments ──
        to_enrich = count_pending_scrape(session)
        if to_enrich > 0 and gap > min_enrich_interval:
            enrich_wait = max(gap / to_enrich, min_enrich_interval)
            enrich_wait *= random.uniform(0.8, 1.2)
            enrich_wait = min(enrich_wait, gap)  # don't overshoot
            logger.info(
                colored("enrich", "yellow")
                + " in %.0fs (gap %.0fs, %d to enrich)",
                enrich_wait, gap, to_enrich,
            )
            time.sleep(enrich_wait)
            if enrich_lane.can_execute():
                logger.info(colored("▶ enrich", "yellow", attrs=["bold"]))
                enrich_lane.execute()
            continue  # re-evaluate gap + to_enrich

        # ── Wait for major action ──
        if gap > 0:
            logger.info(
                colored("next: %s", "white") + " in %.0fs",
                next_schedule.name, gap,
            )
            time.sleep(gap)

        if next_schedule.lane.can_execute():
            color = LANE_COLORS[next_schedule.name]
            logger.info(colored(f"▶ {next_schedule.name}", color, attrs=["bold"]))
            next_schedule.lane.execute()
        next_schedule.reschedule()
