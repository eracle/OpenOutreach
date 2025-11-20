# linkedin/workflow.py
import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler

from .campaigns import load_campaigns, ParsedCampaign, ExecutableStep
from .database import db_manager

logger = logging.getLogger(__name__)

# Global scheduler
SCHEDULER: Optional[BackgroundScheduler] = None


def start_campaign(campaign: ParsedCampaign, start_from_step: int = 0) -> None:
    """Start a full campaign (usually called once at boot)"""
    logger.info(f"Launching campaign: {campaign.name}")
    execute_step(campaign=campaign, profile=None, step_index=start_from_step)


def execute_step(campaign: ParsedCampaign, profile: Optional[Dict[str, Any]], step_index: int) -> None:
    """Core engine: execute one step for one profile (or None for scrape steps)"""
    if step_index >= len(campaign.steps):
        if profile:
            logger.info(f"Workflow completed for {profile.get('linkedin_url')}")
        return

    step: ExecutableStep = campaign.steps[step_index]

    if step.type == "scrape":
        # Scrape steps return a list of enriched profiles
        enriched_profiles = step.execute(context={}, profile=None)
        if not enriched_profiles:
            logger.warning("Scrape step returned no profiles → campaign stopped")
            return

        for prof in enriched_profiles:
            execute_step(campaign, profile=prof, step_index=step_index + 1)
        return

    if step.type == "action":
        if not profile:
            logger.error("Action step received no profile")
            return
        step.execute(context={}, profile=profile)
        execute_step(campaign, profile, step_index + 1)
        return

    if step.type == "condition":
        if not profile:
            logger.error("Condition step received no profile")
            return

        url = profile["linkedin_url"]
        job_id = f"cond_{campaign.name}_{step_index}_{abs(hash(url)) & 0xFFFFFF}"

        SCHEDULER.add_job(
            check_condition_job,
            trigger="date",
            run_date=datetime.now() + timedelta(seconds=15),  # first check soon
            id=job_id,
            replace_existing=True,
            kwargs={
                "campaign_name": campaign.name,
                "profile_url": url,
                "step_index": step_index,
                "attempt": 1,
            },
        )
        logger.info(f"Condition scheduled → {url} (job {job_id})")


def check_condition_job(campaign_name: str, profile_url: str, step_index: int, attempt: int) -> None:
    """Runs inside APScheduler — checks condition and either proceeds or reschedules"""
    campaign = next((c for c in load_campaigns() if c.name == campaign_name), None)
    if not campaign:
        logger.error(f"Campaign {campaign_name} vanished during condition check")
        return

    step: ExecutableStep = campaign.steps[step_index]
    profile = {"linkedin_url": profile_url}

    is_met = step.execute(context={}, profile=profile)

    if is_met:
        logger.info(f"Condition met for {profile_url} after {attempt} attempt(s) → next step")
        execute_step(campaign, profile=profile, step_index=step_index + 1)
        return

    # Not met yet → decide next check
    interval = step.check_interval or timedelta(hours=6)
    next_check = datetime.now() + interval

    # Timeout check
    if step.timeout and (attempt * interval) >= step.timeout:
        logger.warning(f"Timeout exceeded for {profile_url} — abandoning remaining steps")
        return

    job_id = f"cond_{campaign_name}_{step_index}_{abs(hash(profile_url)) & 0xFFFFFF}"
    SCHEDULER.add_job(
        check_condition_job,
        trigger="date",
        run_date=next_check,
        id=job_id,
        replace_existing=True,
        kwargs={
            "campaign_name": campaign_name,
            "profile_url": profile_url,
            "step_index": step_index,
            "attempt": attempt + 1,
        },
    )
    logger.info(f"Condition not met → recheck in {interval} (attempt {attempt + 1})")


def main(db_url: str = "sqlite:///linkedin.db") -> None:
    global SCHEDULER

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )

    logger.info("Initializing database…")
    db_manager.init_db(db_url)
    db_manager.create_tables()

    logger.info("Starting persistent APScheduler…")
    jobstores = {"default": SQLAlchemyJobStore(url=db_url)}
    executors = {"default": ThreadPoolExecutor(10)}
    SCHEDULER = BackgroundScheduler(jobstores=jobstores, executors=executors)
    SCHEDULER.start()

    logger.info("Loading campaigns…")
    campaigns = load_campaigns()
    if not campaigns:
        logger.error("No campaigns found → exiting")
        return

    # Start the first (and usually only) campaign
    start_campaign(campaigns[0])

    logger.info("Engine is running! Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down…")
        SCHEDULER.shutdown(wait=True)
        logger.info("Bye!")


if __name__ == "__main__":
    main()
