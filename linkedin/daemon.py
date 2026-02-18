# linkedin/daemon.py
from __future__ import annotations

import logging
import random
import time
from termcolor import colored

from linkedin.conf import CAMPAIGN_CONFIG, MODEL_PATH
from linkedin.db.crm_profiles import count_leads_for_qualification, pipeline_needs_refill, seed_promo_deals
from linkedin.lanes.check_pending import CheckPendingLane
from linkedin.lanes.connect import ConnectLane
from linkedin.lanes.follow_up import FollowUpLane
from linkedin.lanes.search import SearchLane
from linkedin.ml.qualifier import BayesianQualifier
from linkedin.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)



class LaneSchedule:
    """Tracks when a major lane should next fire."""

    def __init__(self, name: str, lane, base_interval_seconds: float, campaign=None):
        self.name = name
        self.lane = lane
        self.base_interval = base_interval_seconds
        self.campaign = campaign
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
    from linkedin.management.setup_crm import ensure_campaign_pipeline
    from linkedin.ml.embeddings import ensure_embeddings_table, get_labeled_data
    from linkedin.ml.hub import get_kit, import_promo_campaign

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

    # Shared rate limiters across all campaigns
    connect_limiter = RateLimiter(
        daily_limit=lp.connect_daily_limit,
        weekly_limit=lp.connect_weekly_limit,
    )
    follow_up_limiter = RateLimiter(
        daily_limit=lp.follow_up_daily_limit,
    )

    # Load kit model for promo campaigns
    kit = get_kit()
    if kit:
        import_promo_campaign(kit["config"])
    kit_model = kit["model"] if kit else None

    check_pending_interval = cfg["check_pending_recheck_after_hours"] * 3600
    min_enrich_interval = cfg["enrich_min_interval"]
    min_action_interval = cfg["min_action_interval"]
    min_qualifiable_leads = cfg["min_qualifiable_leads"]

    # Build schedules for ALL campaigns
    all_schedules = []
    qualify_lane = None
    search_lane = None

    for campaign in session.campaigns:
        session.campaign = campaign
        ensure_campaign_pipeline(campaign.department)

        if campaign.is_promo:
            connect_lane = ConnectLane(session, connect_limiter, qualifier, pipeline=kit_model)
            check_pending_lane = CheckPendingLane(session, cfg["check_pending_recheck_after_hours"])
            follow_up_lane = FollowUpLane(session, follow_up_limiter)

            all_schedules.extend([
                LaneSchedule("connect", connect_lane, min_action_interval, campaign=campaign),
                LaneSchedule("check_pending", check_pending_lane, check_pending_interval, campaign=campaign),
                LaneSchedule("follow_up", follow_up_lane, min_action_interval, campaign=campaign),
            ])
        else:
            connect_lane = ConnectLane(session, connect_limiter, qualifier)
            check_pending_lane = CheckPendingLane(session, cfg["check_pending_recheck_after_hours"])
            follow_up_lane = FollowUpLane(session, follow_up_limiter)

            # Qualify and search lanes are only for non-promo campaigns
            qualify_lane = QualifyLane(session, qualifier)
            search_lane = SearchLane(session, qualifier)

            all_schedules.extend([
                LaneSchedule("connect", connect_lane, min_action_interval, campaign=campaign),
                LaneSchedule("check_pending", check_pending_lane, check_pending_interval, campaign=campaign),
                LaneSchedule("follow_up", follow_up_lane, min_action_interval, campaign=campaign),
            ])

    if not all_schedules:
        logger.error("No campaigns found — cannot start daemon")
        return

    logger.info(
        colored("Daemon started", "green", attrs=["bold"])
        + " — %d campaigns, action interval %ds, check_pending every %.0fm",
        len(list(session.campaigns)),
        min_action_interval,
        check_pending_interval / 60,
    )

    while True:
        # ── Find soonest major action ──
        now = time.time()
        next_schedule = min(all_schedules, key=lambda s: s.next_run)
        gap = max(next_schedule.next_run - now, 0)

        # ── Fill gap with search (pipeline low) + qualifications (non-promo only) ──
        has_non_promo = qualify_lane is not None and search_lane is not None
        if has_non_promo and gap > min_enrich_interval:
            # Set campaign to the non-promo one for gap-filling
            non_promo = next(c for c in session.campaigns if not c.is_promo)
            session.campaign = non_promo

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

        # Set active campaign for this schedule
        session.campaign = next_schedule.campaign

        # Probabilistic gating for promo campaigns
        if next_schedule.campaign.is_promo:
            if random.random() >= next_schedule.campaign.action_fraction:
                next_schedule.reschedule()
                continue
            seed_promo_deals(session)

        if next_schedule.lane.can_execute():
            next_schedule.lane.execute()
            next_schedule.reschedule()
        else:
            # Nothing to do — retry soon instead of waiting the full interval
            next_schedule.next_run = time.time() + 60
