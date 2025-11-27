# linkedin/workflow.py
import csv
import hashlib
import logging
import time
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import create_engine

from linkedin.automation import AutomationRegistry
from linkedin.campaigns import campaigns
from linkedin.conf import DATA_DIR

logger = logging.getLogger(__name__)


# ——————————————————————————————————————————————————————————————
# GLOBAL PICKLE-SAFE JOB FUNCTION — FULLY PERSISTENT
# ——————————————————————————————————————————————————————————————
def run_campaign_step(
    handle: str,
    campaign_name: str,
    csv_hash: str,
    profile_data: dict,
    step_index: int,
    job_id: str,
    input_csv_path: str,
    output_csv_template: str = None,
):
    automation = AutomationRegistry.get_or_create(
        handle=handle,
        campaign_name=campaign_name,
        csv_hash=csv_hash,
        input_csv=Path(input_csv_path),
        output_csv_template=output_csv_template,
    )
    campaign = campaigns.get(campaign_name)
    if not campaign:
        logger.error(f"Campaign {campaign_name} not found!")
        return

    profile = profile_data.copy()

    try:
        if step_index >= len(campaign.steps):
            automation.append_to_csv(profile)
            logger.info(f"COMPLETED {profile.get('full_name') or profile['linkedin_url']}")
            return

        step = campaign.steps[step_index]
        method = getattr(automation, step.handler)

        logger.info(
            f"[{handle}] Step {step_index + 1}/{len(campaign.steps)} → {step.handler} | "
            f"{profile.get('full_name') or profile['linkedin_url']}"
        )

        if step.type == "condition":
            accepted = method(profile, step.config or {})
            if not accepted:
                delay = step.check_interval or timedelta(hours=6)
                _reschedule_job(
                    handle, campaign_name, csv_hash, profile,
                    step_index, job_id, delay,
                    input_csv_path, output_csv_template
                )
                logger.info(f"Condition failed → retry in {delay}")
                return

        elif step.type == "scrape":
            result = method(profile)
            if result:
                profile.update(result)

        else:  # action
            method(profile, step.config or {})

        # Success → next step
        delay = timedelta(seconds=random.uniform(3, 10))
        _reschedule_job(
            handle, campaign_name, csv_hash, profile,
            step_index + 1, job_id, delay,
            input_csv_path, output_csv_template
        )

    except Exception as e:
        logger.error(f"FAILED {profile.get('linkedin_url', 'unknown')} at step {step_index}", exc_info=True)
        automation.mark_failed(profile.get("linkedin_url", "unknown"), str(e))


def _reschedule_job(
    handle, campaign_name, csv_hash, profile, step_index, job_id,
    delay, input_csv_path, output_csv_template
):
    from linkedin.workflow import run_campaign_step

    db_path = DATA_DIR / f"campaign_{handle}+{campaign_name}+{csv_hash}.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True, connect_args={"timeout": 30})

    scheduler = BackgroundScheduler(
        jobstores={"default": SQLAlchemyJobStore(engine=engine)},
        job_defaults={"coalesce": True, "max_instances": 1},
    )
    scheduler.start()

    scheduler.add_job(
        run_campaign_step,
        trigger="date",
        run_date=datetime.now() + delay,
        id=job_id,
        replace_existing=True,
        args=(
            handle, campaign_name, csv_hash,
            profile, step_index, job_id,
            input_csv_path, output_csv_template
        ),
    )
    # job stays in DB → persistence!


# ——————————————————————————————————————————————————————————————
# Helpers
# ——————————————————————————————————————————————————————————————
def compute_csv_hash(csv_path: Path) -> str:
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)
    hasher = hashlib.sha256()
    with open(csv_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()[:12]


def load_profiles_from_csv(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")
    seen = set()
    profiles = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if "url" not in reader.fieldnames:
            raise ValueError("CSV must contain 'url' column")
        for row in reader:
            url = row["url"].strip().rstrip("/")
            if url and url.lower() not in seen:
                seen.add(url.lower())
                clean_url = url if url.endswith("/") else url + "/"
                profiles.append({"linkedin_url": clean_url, **row})
    logger.info(f"Loaded {len(profiles)} unique profiles from {csv_path.name}")
    return profiles


# ——————————————————————————————————————————————————————————————
# Core Engine
# ——————————————————————————————————————————————————————————————
class LinkedInCampaignEngine:
    def __init__(self, handle: str, campaign_name: str, input_csv: str | Path):
        self.handle = handle
        self.campaign_name = campaign_name
        self.input_csv = Path(input_csv).expanduser().resolve()

        self.campaign = campaigns.get(campaign_name)
        if not self.campaign:
            available = ", ".join(campaigns._registry.keys())
            raise ValueError(f"Campaign '{campaign_name}' not found. Available: {available}")

        self.csv_hash = compute_csv_hash(self.input_csv)
        self.db_path = DATA_DIR / f"campaign_{handle}+{campaign_name}+{self.csv_hash}.db"

        self.output_csv_template = self.campaign.settings.get(
            "output_csv",
            f"./assets/output/enriched_{campaign_name}+{self.csv_hash}.csv"
        )

        self.automation = AutomationRegistry.get_or_create(
            handle=handle,
            campaign_name=campaign_name,
            csv_hash=self.csv_hash,
            input_csv=self.input_csv,
            output_csv_template=self.output_csv_template,
        )

        self.scheduler: Optional[BackgroundScheduler] = None

    def start(self) -> "LinkedInCampaignEngine":
        from linkedin.workflow import run_campaign_step

        engine = create_engine(f"sqlite:///{self.db_path}", future=True, connect_args={"timeout": 30})
        jobstore = SQLAlchemyJobStore(engine=engine)

        self.scheduler = BackgroundScheduler(
            jobstores={"default": jobstore},
            executors={"default": ThreadPoolExecutor(1)},
            job_defaults={"coalesce": True, "max_instances": 1},
        )
        self.scheduler.start()

        profiles = load_profiles_from_csv(self.input_csv)
        existing_job_ids = {job.id for job in self.scheduler.get_jobs()}
        added = 0

        for i, profile in enumerate(profiles):
            url = profile["linkedin_url"]
            job_id = f"{self.automation.key()}::{hash(url)}"

            if job_id in existing_job_ids:
                continue

            delay_start = timedelta(seconds=i * 2 + random.uniform(1, 6))
            self.scheduler.add_job(
                run_campaign_step,
                trigger="date",
                run_date=datetime.now() + delay_start,
                id=job_id,
                replace_existing=False,
                args=(
                    self.handle,
                    self.campaign_name,
                    self.csv_hash,
                    profile,
                    0,
                    job_id,
                    str(self.input_csv),
                    self.output_csv_template,
                ),
            )
            added += 1

        logger.info(f"Campaign started → {added} new jobs scheduled, {len(existing_job_ids)} resumed")
        return self

    def stop(self):
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=True)
            logger.info("Scheduler stopped")

    def status(self) -> dict:
        jobs = self.scheduler.get_jobs() if self.scheduler else []
        pending = len([j for j in jobs if j.next_run_time])
        return {
            "pending": pending,
            "output_path": self.automation.output_path,
        }


def start_or_resume_campaign(handle: str, campaign_name: str, input_csv: str | Path):
    return LinkedInCampaignEngine(handle, campaign_name, input_csv).start()


# ——————————————————————————————————————————————————————————————
# CLI
# ——————————————————————————————————————————————————————————————
if __name__ == "__main__":
    import logging

    logging.getLogger().handlers.clear()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s │ %(levelname)-8s │ %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        engine = start_or_resume_campaign(
            handle="eracle",
            campaign_name="linked_in_connect_follow_up",
            input_csv="./assets/inputs/urls.csv",
        )

        print("\nLinkedIn Campaign Engine STARTED & FULLY PERSISTENT")
        print(f"   Account  : {engine.handle}")
        print(f"   Campaign : {engine.campaign_name}")
        print(f"   Output   : {engine.automation.output_path}")
        print(f"   DB       : {engine.db_path.name}")
        print("   Jobs survive Ctrl+C, crashes, and reboots!\n")

        while True:
            time.sleep(30)
            s = engine.status()
            print(f"Pending: {s['pending']:<4} │ Writing → {s['output_path'].name}")

    except KeyboardInterrupt:
        print("\n\nShutting down gracefully...")
        engine.stop()
        print("Goodbye!")

    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        raise