# linkedin/daemon.py
from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timedelta

from termcolor import colored

from linkedin.conf import CAMPAIGN_CONFIG
from linkedin.db.crm_profiles import count_enriched_profiles, count_pending_scrape
from linkedin.lanes.check_pending import CheckPendingLane
from linkedin.lanes.connect import ConnectLane
from linkedin.lanes.enrich import EnrichLane
from linkedin.lanes.follow_up import FollowUpLane
from linkedin.lanes.search import SearchLane
from linkedin.ml.qualifier import BayesianQualifier
from linkedin.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)



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
    from linkedin.lanes.qualify import QualifyLane
    from linkedin.ml.embeddings import ensure_embeddings_table, get_labeled_data

    cfg = CAMPAIGN_CONFIG

    # Initialize embeddings table and GPC qualifier
    ensure_embeddings_table()

    qualifier = BayesianQualifier(
        seed=42,
        n_mc_samples=cfg["qualification_n_mc_samples"],
    )
    X, y = get_labeled_data()
    if len(X) > 0:
        qualifier.warm_start(X, y)
        logger.info(
            colored("GPC qualifier warm-started", "cyan")
            + " on %d labelled samples (%d positive, %d negative)",
            len(y), int((y == 1).sum()), int((y == 0).sum()),
        )

    qualify_lane = QualifyLane(session, qualifier)

    connect_limiter = RateLimiter(
        daily_limit=cfg["connect_daily_limit"],
        weekly_limit=cfg["connect_weekly_limit"],
    )
    follow_up_limiter = RateLimiter(
        daily_limit=cfg["follow_up_daily_limit"],
    )

    enrich_lane = EnrichLane(session)
    connect_lane = ConnectLane(session, connect_limiter, qualifier)
    check_pending_lane = CheckPendingLane(session, cfg["check_pending_recheck_after_hours"])
    follow_up_lane = FollowUpLane(session, follow_up_limiter)
    search_lane = SearchLane(session, qualifier)

    # Working hours
    wh_start = _parse_time(cfg["working_hours_start"])
    wh_end = _parse_time(cfg["working_hours_end"])
    check_pending_interval = cfg["check_pending_recheck_after_hours"] * 3600
    min_enrich_interval = cfg["enrich_min_interval"]
    min_action_interval = cfg["min_action_interval"]

    schedules = [
        LaneSchedule("connect", connect_lane, min_action_interval),
        LaneSchedule("check_pending", check_pending_lane, check_pending_interval),
        LaneSchedule("follow_up", follow_up_lane, min_action_interval),
    ]

    logger.info(
        colored("Daemon started", "green", attrs=["bold"])
        + " — working hours %s–%s, action interval %ds, check_pending every %.0fm",
        cfg["working_hours_start"],
        cfg["working_hours_end"],
        min_action_interval,
        check_pending_interval / 60,
    )

    while True:
        # ── Working hours gate ──
        if not _in_working_hours(wh_start, wh_end):
            wait = _seconds_until_work_starts(wh_start)
            logger.info(
                colored("Outside working hours", "yellow")
                + " — sleeping until %02d:%02d",
                wh_start[0], wh_start[1],
            )
            time.sleep(wait)
            for s in schedules:
                s.next_run = time.time()  # fire immediately in new window
            continue

        # ── Find soonest major action ──
        now = time.time()
        next_schedule = min(schedules, key=lambda s: s.next_run)
        gap = max(next_schedule.next_run - now, 0)

        # ── Fill gap with enrichments + qualifications + search ──
        if gap > min_enrich_interval:
            # Count *before* computing wait, but re-check *after* sleep
            to_enrich = count_pending_scrape(session)
            to_qualify = count_enriched_profiles(session)
            total_work = to_enrich + to_qualify

            if total_work > 0:
                enrich_wait = max(gap / total_work, min_enrich_interval)
                enrich_wait *= random.uniform(0.8, 1.2)
                enrich_wait = min(enrich_wait, gap)  # don't overshoot
                logger.debug(
                    "gap-fill in %.0fs (gap %.0fs, %d to enrich, %d to qualify)",
                    enrich_wait, gap, to_enrich, to_qualify,
                )
                time.sleep(enrich_wait)

                # Fresh check after sleep — counts may have changed
                if enrich_lane.can_execute():
                    enrich_lane.execute()
                    continue  # re-evaluate gap
                elif qualify_lane.can_execute():
                    qualify_lane.execute()
                    continue  # re-evaluate gap

            # Pipeline empty — search for new profiles
            if search_lane.can_execute():
                search_lane.execute()
                continue  # re-evaluate gap

        # ── Wait for major action ──
        if gap > 0:
            logger.debug(
                "next: %s in %.0fs",
                next_schedule.name, gap,
            )
            time.sleep(gap)

        if next_schedule.lane.can_execute():
            next_schedule.lane.execute()
            next_schedule.reschedule()
        else:
            # Nothing to do — retry soon instead of waiting the full interval
            next_schedule.next_run = time.time() + 60
