# linkedin/daemon.py
from __future__ import annotations

import logging
import random
import time
from termcolor import colored

from linkedin.conf import CAMPAIGN_CONFIG, _LEGACY_MODEL_PATH, model_path_for_campaign
from linkedin.db.crm_profiles import count_leads_for_qualification, pipeline_needs_refill, seed_partner_deals
from linkedin.lanes.check_pending import CheckPendingLane
from linkedin.lanes.connect import ConnectLane
from linkedin.lanes.follow_up import FollowUpLane
from linkedin.lanes.search import SearchLane
from linkedin.ml.qualifier import BayesianQualifier

logger = logging.getLogger(__name__)

class _PromoRotator:
    """Logs rotating promotional messages every *every* lane executions."""

    _MESSAGES = [
        colored("Join the community or give direct feedback on Telegram \u2192 https://t.me/+Y5bh9Vg8UVg5ODU0", "blue", attrs=["bold"]),
        "\033[38;5;208;1mLove OpenOutreach? Sponsor the project \u2192 https://github.com/sponsors/eracle\033[0m",
    ]

    def __init__(self, every: int = 10):
        self._every = every
        self._ticks = 0
        self._next = 0

    def tick(self):
        self._ticks += 1
        if self._ticks % self._every == 0:
            logger.info(self._MESSAGES[self._next % len(self._MESSAGES)])
            self._next += 1



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


def _migrate_legacy_model(campaigns):
    """Migrate old global model.joblib to per-campaign path if possible."""
    if not _LEGACY_MODEL_PATH.exists():
        return

    non_partner = [c for c in campaigns if not c.is_partner]
    if len(non_partner) == 1:
        dest = model_path_for_campaign(non_partner[0].pk)
        if dest.exists():
            logger.info("Legacy model.joblib exists but %s already present — skipping migration", dest.name)
            return
        _LEGACY_MODEL_PATH.rename(dest)
        logger.info("Migrated legacy model.joblib → %s", dest.name)
    else:
        logger.warning(
            "Legacy model.joblib found but %d non-partner campaigns exist — "
            "cannot auto-migrate. Remove it manually once per-campaign models are trained.",
            len(non_partner),
        )


def _build_qualifiers(campaigns, cfg):
    """Create per-campaign BayesianQualifiers and a shared partner qualifier.

    Returns (qualifiers, partner_qualifier) where qualifiers is a
    dict[int, BayesianQualifier] keyed by campaign PK (non-partner only).
    """
    from linkedin.ml.embeddings import get_labeled_data

    X, y = get_labeled_data()

    qualifiers: dict[int, BayesianQualifier] = {}
    for campaign in campaigns:
        if campaign.is_partner:
            continue
        q = BayesianQualifier(
            seed=42,
            n_mc_samples=cfg["qualification_n_mc_samples"],
            save_path=model_path_for_campaign(campaign.pk),
        )
        if len(X) > 0:
            q.warm_start(X, y)
        qualifiers[campaign.pk] = q

    if qualifiers and len(X) > 0:
        logger.info(
            colored("GP qualifiers warm-started", "cyan")
            + " on %d labelled samples (%d positive, %d negative)"
            + " for %d campaign(s)",
            len(y), int((y == 1).sum()), int((y == 0).sum()), len(qualifiers),
        )

    partner_qualifier = BayesianQualifier(
        seed=42,
        n_mc_samples=cfg["qualification_n_mc_samples"],
        save_path=None,
    )
    return qualifiers, partner_qualifier


def run_daemon(session):
    from linkedin.lanes.qualify import QualifyLane
    from linkedin.management.setup_crm import ensure_campaign_pipeline
    from linkedin.ml.embeddings import ensure_embeddings_table
    from linkedin.ml.hub import get_kit, import_partner_campaign

    cfg = CAMPAIGN_CONFIG

    # Initialize embeddings table
    ensure_embeddings_table()

    # Load kit model for partner campaigns
    kit = get_kit()
    if kit:
        import_partner_campaign(kit["config"])
    kit_model = kit["model"] if kit else None

    # Migrate legacy single model file before creating per-campaign qualifiers
    _migrate_legacy_model(list(session.campaigns))

    qualifiers, partner_qualifier = _build_qualifiers(session.campaigns, cfg)

    check_pending_interval = cfg["check_pending_recheck_after_hours"] * 3600
    min_enrich_interval = cfg["enrich_min_interval"]
    min_action_interval = cfg["min_action_interval"]
    min_qualifiable_leads = cfg["min_qualifiable_leads"]

    # Compute partner action_fraction (max across all partner campaigns)
    partner_fraction = max(
        (c.action_fraction for c in session.campaigns if c.is_partner),
        default=0.0,
    )

    # Build schedules for ALL campaigns
    all_schedules = []
    qualify_lanes: dict[int, QualifyLane] = {}
    search_lanes: dict[int, SearchLane] = {}

    for campaign in session.campaigns:
        session.campaign = campaign
        ensure_campaign_pipeline(campaign.department)

        if campaign.is_partner:
            connect_lane = ConnectLane(session, partner_qualifier, pipeline=kit_model)
            check_pending_lane = CheckPendingLane(session, cfg["check_pending_recheck_after_hours"])
            follow_up_lane = FollowUpLane(session)

            all_schedules.extend([
                LaneSchedule("connect", connect_lane, min_action_interval, campaign=campaign),
                LaneSchedule("check_pending", check_pending_lane, check_pending_interval, campaign=campaign),
                LaneSchedule("follow_up", follow_up_lane, min_action_interval, campaign=campaign),
            ])
        else:
            qualifier = qualifiers[campaign.pk]
            connect_lane = ConnectLane(session, qualifier)
            check_pending_lane = CheckPendingLane(session, cfg["check_pending_recheck_after_hours"])
            follow_up_lane = FollowUpLane(session)

            qualify_lanes[campaign.pk] = QualifyLane(session, qualifier)
            search_lanes[campaign.pk] = SearchLane(session, qualifier)

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

    promo = _PromoRotator(every=2)

    while True:
        # ── Find soonest major action ──
        now = time.time()
        next_schedule = min(all_schedules, key=lambda s: s.next_run)
        gap = max(next_schedule.next_run - now, 0)

        # ── Fill gap with search (pipeline low) + qualifications (non-partner only) ──
        if qualify_lanes and gap > min_enrich_interval:
            # Pick the first non-partner campaign for gap-filling
            non_partner = next(c for c in session.campaigns if not c.is_partner)
            session.campaign = non_partner
            ql = qualify_lanes[non_partner.pk]
            sl = search_lanes[non_partner.pk]

            if pipeline_needs_refill(session, min_qualifiable_leads):
                if sl.can_execute():
                    sl.execute()
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

                if ql.can_execute():
                    ql.execute()
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

        # Probabilistic gating for partner campaigns
        if next_schedule.campaign.is_partner:
            if random.random() >= next_schedule.campaign.action_fraction:
                next_schedule.reschedule()
                continue
            seed_partner_deals(session)

        # Inverse gating: skip regular campaigns proportionally to partner action_fraction
        if not next_schedule.campaign.is_partner and partner_fraction > 0:
            if random.random() < partner_fraction:
                next_schedule.reschedule()
                continue

        if next_schedule.lane.can_execute():
            next_schedule.lane.execute()
            next_schedule.reschedule()
            promo.tick()
        else:
            # Nothing to do — retry soon instead of waiting the full interval
            next_schedule.next_run = time.time() + 60
