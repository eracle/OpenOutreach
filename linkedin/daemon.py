# linkedin/daemon.py
from __future__ import annotations

import logging
import random
import time
from termcolor import colored

from linkedin.conf import CAMPAIGN_CONFIG, MODEL_PATH
from linkedin.db.crm_profiles import count_leads_for_qualification, pipeline_needs_refill
from linkedin.lanes.check_pending import CheckPendingLane
from linkedin.lanes.connect import ConnectLane
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
    lp = session.linkedin_profile

    # Initialize embeddings table and GPC qualifier
    ensure_embeddings_table()

    qualifier = BayesianQualifier(
        seed=42,
        n_mc_samples=cfg["qualification_n_mc_samples"],
        save_path=MODEL_PATH,
    )
    X, y = get_labeled_data()
    if len(X) > 0:
        qualifier.warm_start(X, y)
        logger.info(
            colored("GP qualifier warm-started", "cyan")
            + " on %d labelled samples (%d positive, %d negative)",
            len(y), int((y == 1).sum()), int((y == 0).sum()),
        )

    qualify_lane = QualifyLane(session, qualifier)

    connect_limiter = RateLimiter(
        daily_limit=lp.connect_daily_limit,
        weekly_limit=lp.connect_weekly_limit,
    )
    follow_up_limiter = RateLimiter(
        daily_limit=lp.follow_up_daily_limit,
    )

    connect_lane = ConnectLane(session, connect_limiter, qualifier)
    check_pending_lane = CheckPendingLane(session, cfg["check_pending_recheck_after_hours"])
    follow_up_lane = FollowUpLane(session, follow_up_limiter)
    search_lane = SearchLane(session, qualifier)

    check_pending_interval = cfg["check_pending_recheck_after_hours"] * 3600
    min_enrich_interval = cfg["enrich_min_interval"]
    min_action_interval = cfg["min_action_interval"]
    min_qualifiable_leads = cfg["min_qualifiable_leads"]

    schedules = [
        LaneSchedule("connect", connect_lane, min_action_interval),
        LaneSchedule("check_pending", check_pending_lane, check_pending_interval),
        LaneSchedule("follow_up", follow_up_lane, min_action_interval),
    ]

    _hub = __import__("linkedin.ml.hub", fromlist=["after_action", "get_action_fraction"])
    _post = getattr(_hub, "after_action", None)
    _kit_fraction = getattr(_hub, "get_action_fraction", lambda: 0.0)()

    logger.info(
        colored("Daemon started", "green", attrs=["bold"])
        + " — action interval %ds, check_pending every %.0fm",
        min_action_interval,
        check_pending_interval / 60,
    )

    while True:
        # ── Find soonest major action ──
        now = time.time()
        next_schedule = min(schedules, key=lambda s: s.next_run)
        gap = max(next_schedule.next_run - now, 0)

        # ── Fill gap with search (pipeline low) + qualifications ──
        if gap > min_enrich_interval:
            if pipeline_needs_refill(session, min_qualifiable_leads):
                if search_lane.can_execute():
                    search_lane.execute()
                    continue

            to_qualify = count_leads_for_qualification(session)
            if to_qualify > 0:
                qualify_wait = max(gap / to_qualify, min_enrich_interval)
                qualify_wait *= random.uniform(0.8, 1.2)
                qualify_wait = min(qualify_wait, gap)
                logger.debug(
                    "gap-fill in %.0fs (gap %.0fs, %d to qualify)",
                    qualify_wait, gap, to_qualify,
                )
                time.sleep(qualify_wait)

                if qualify_lane.can_execute():
                    qualify_lane.execute()
                    continue

        # ── Wait for major action ──
        if gap > 0:
            logger.debug(
                "next: %s in %.0fs",
                next_schedule.name, gap,
            )
            time.sleep(gap)

        use_partner = _post and random.random() < _kit_fraction

        if use_partner:
            _post(session, connect_limiter=connect_limiter, follow_up_limiter=follow_up_limiter)
            next_schedule.reschedule()
        elif next_schedule.lane.can_execute():
            next_schedule.lane.execute()
            next_schedule.reschedule()
        else:
            # Nothing to do — retry soon instead of waiting the full interval
            next_schedule.next_run = time.time() + 60
