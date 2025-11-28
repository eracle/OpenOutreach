# linkedin/workflow.py
import csv
import hashlib
import logging
import random
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import create_engine

from linkedin.account_session import AccountSessionRegistry
from linkedin.campaigns import campaigns
from linkedin.conf import DATA_DIR

logger = logging.getLogger(__name__)

# ——————————————————————————————————————————————————————————————
# CONFIGURABLE CONSTANTS
# ——————————————————————————————————————————————————————————————
FAILURE_RETRY_DELAY = timedelta(hours=2)        # Retry delay for failed profiles
RANDOM_DELAY_MIN = 3                            # Min random delay (seconds)
RANDOM_DELAY_MAX = 10                           # Max random delay (seconds)


# ——————————————————————————————————————————————————————————————
# CORE JOB FUNCTION — PICKLE-SAFE
# ——————————————————————————————————————————————————————————————
def run_campaign_step(
    handle: str,
    campaign_name: str,
    csv_hash: str,
    profile: dict,
    step_index: int,
    job_id: str,
    input_csv_path: str,
    attempt: int = 1,
):
    """
    Pickle-safe job function for APScheduler. Re-creates AccountSessionRegistry to avoid
    serializing unpickleable objects (e.g., Playwright sessions).
    """
    automation = AccountSessionRegistry.get_or_create(
        handle=handle,
        campaign_name=campaign_name,
        csv_hash=csv_hash,
        input_csv=Path(input_csv_path),
    )
    campaign = campaigns.get(campaign_name)
    if not campaign:
        logger.error(f"Campaign {campaign_name} not found")
        return

    profile = profile.copy()
    url = profile["linkedin_url"]

    try:
        if step_index >= len(campaign.steps):
            logger.info(f"COMPLETED {profile.get('full_name') or url}")
            automation.mark_completed(url)
            return

        step = campaign.steps[step_index]
        handler = getattr(automation, step.handler)

        logger.info(
            f"[{handle}] Attempt {attempt} | Step {step_index + 1}/{len(campaign.steps)} → {step.handler} | "
            f"{profile.get('full_name') or url}"
        )

        if step.type == "condition":
            if not handler(profile, step.config or {}):
                delay = step.check_interval or timedelta(hours=6)
                _schedule_next(
                    handle, campaign_name, csv_hash, profile, step_index, job_id,
                    input_csv_path, delay
                )
                logger.info(f"Condition not met → retry in {delay}")
                return

        elif step.type == "scrape":
            result = handler(profile)
            if result:
                profile.update(result)

        else:  # action
            handler(profile, step.config or {})

        # Success → next step with random delay
        delay = timedelta(seconds=random.uniform(RANDOM_DELAY_MIN, RANDOM_DELAY_MAX))
        _schedule_next(
            handle, campaign_name, csv_hash, profile, step_index + 1, job_id,
            input_csv_path, delay
        )

    except Exception as e:
        logger.error(f"FAILED {url} | attempt {attempt} | step {step_index}", exc_info=True)

        # Retry forever with fixed backoff
        _schedule_next(
            handle, campaign_name, csv_hash, profile, step_index, job_id,
            input_csv_path, FAILURE_RETRY_DELAY, attempt=attempt + 1
        )


def _schedule_next(
    handle, campaign_name, csv_hash, profile, step_index, job_id,
    input_csv_path, delay, attempt=1
):
    """Schedule the next job using the global scheduler."""
    scheduler = LinkedInCampaignEngine.get_scheduler(
        handle=handle,
        campaign_name=campaign_name,
        csv_hash=csv_hash,
        input_csv_path=input_csv_path,
    )
    scheduler.add_job(
        run_campaign_step,
        trigger="date",
        run_date=datetime.now() + delay,
        id=job_id,
        replace_existing=True,
        args=(
            handle, campaign_name, csv_hash,
            profile, step_index, job_id,
            input_csv_path, attempt
        ),
    )


# ——————————————————————————————————————————————————————————————
# HELPERS
# ——————————————————————————————————————————————————————————————
def compute_csv_hash(csv_path: Path) -> str:
    hasher = hashlib.sha256()
    with open(csv_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()[:12]


def load_unique_profiles(csv_path: Path) -> List[Dict]:
    seen = set()
    profiles = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if "url" not in reader.fieldnames:
            raise ValueError("CSV must have 'url' column")
        for row in reader:
            url = row["url"].strip().rstrip("/")
            if not url or url.lower() in seen:
                continue
            seen.add(url.lower())
            clean_url = url if url.endswith("/") else url + "/"
            profiles.append({"linkedin_url": clean_url, **row})
    logger.info(f"Loaded {len(profiles)} unique profiles from {csv_path.name}")
    return profiles


# ——————————————————————————————————————————————————————————————
# MAIN ENGINE
# ——————————————————————————————————————————————————————————————
class LinkedInCampaignEngine:
    _schedulers: Dict[str, BackgroundScheduler] = {}
    _db_paths: Dict[str, Path] = {}

    def __init__(self, handle: str, campaign_name: str, input_csv: str | Path):
        self.handle = handle
        self.campaign_name = campaign_name
        self.input_csv = Path(input_csv).expanduser().resolve()

        if not campaigns.get(campaign_name):
            available = ", ".join(campaigns._registry.keys())
            raise ValueError(f"Campaign '{campaign_name}' not found. Available: {available}")

        self.csv_hash = compute_csv_hash(self.input_csv)
        self.key = f"{handle}+{campaign_name}+{self.csv_hash}"
        self.db_path = DATA_DIR / f"campaign_{self.key}.db"
        self._db_paths[self.key] = self.db_path

    @classmethod
    def get_scheduler(cls, handle, campaign_name, csv_hash, input_csv_path):
        key = f"{handle}+{campaign_name}+{csv_hash}"
        if key not in cls._schedulers:
            raise RuntimeError(f"Scheduler not started for {key}. Call .start() first.")
        return cls._schedulers[key]

    def start(self) -> "LinkedInCampaignEngine":
        """
        Start or resume the campaign. If the DB exists, resume existing jobs without
        adding new profiles. If no DB exists, create it and schedule new jobs.
        """
        scheduler = self._schedulers.get(self.key)
        if scheduler and scheduler.running:
            logger.info(f"Resuming existing campaign {self.key}")
            return self

        # Check if DB exists before creating engine
        if self.db_path.exists():
            logger.info(f"DB exists at {self.db_path.name} → resuming existing jobs")
            engine = create_engine(f"sqlite:///{self.db_path}", future=True, connect_args={"timeout": 30})
            jobstore = SQLAlchemyJobStore(engine=engine)
            scheduler = BackgroundScheduler(
                jobstores={"default": jobstore},
                executors={"default": ThreadPoolExecutor(1)},
                job_defaults={"coalesce": True, "max_instances": 1},
            )
            scheduler.start()
            self._schedulers[self.key] = scheduler
            existing = len(scheduler.get_jobs())
            logger.info(f"Resumed {existing} existing jobs")
        else:
            logger.info(f"No DB found → creating new campaign at {self.db_path.name}")
            engine = create_engine(f"sqlite:///{self.db_path}", future=True, connect_args={"timeout": 30})
            jobstore = SQLAlchemyJobStore(engine=engine)
            scheduler = BackgroundScheduler(
                jobstores={"default": jobstore},
                executors={"default": ThreadPoolExecutor(1)},
                job_defaults={"coalesce": True, "max_instances": 1},
            )
            scheduler.start()
            self._schedulers[self.key] = scheduler

            # Load and schedule new profiles
            profiles = load_unique_profiles(self.input_csv)
            for i, profile in enumerate(profiles):
                url = profile["linkedin_url"]
                job_id = f"{self.key}::{hash(url)}"
                scheduler.add_job(
                    run_campaign_step,
                    trigger="date",
                    run_date=datetime.now() + timedelta(seconds=i * 2 + random.uniform(1, 6)),
                    id=job_id,
                    replace_existing=False,
                    args=(
                        self.handle, self.campaign_name, self.csv_hash,
                        profile, 0, job_id, str(self.input_csv), 1
                    ),
                )
            logger.info(f"Scheduled {len(profiles)} new profiles")

        return self

    def status(self) -> dict:
        scheduler = self._schedulers.get(self.key)
        if not scheduler or not scheduler.running:
            return {"pending": 0, "completed": 0, "total": 0, "db": self.db_path.name, "status": "not_started"}
        jobs = scheduler.get_jobs()
        pending = len([j for j in jobs if j.next_run_time])
        completed = len([j for j in jobs if not j.next_run_time])
        return {
            "pending": pending,
            "completed": completed,
            "total": len(jobs),
            "db": self.db_path.name,
            "status": "running",
        }

    def stop(self):
        scheduler = self._schedulers.pop(self.key, None)
        if scheduler and scheduler.running:
            scheduler.shutdown(wait=True)
            logger.info(f"Scheduler stopped for {self.key}")


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
        engine = LinkedInCampaignEngine(
            handle="eracle",
            campaign_name="linked_in_connect_follow_up",
            input_csv="./assets/inputs/urls.csv",
        ).start()

        print("\nLinkedIn Campaign Engine STARTED & FULLY PERSISTENT")
        print(f"   Account  : {engine.handle}")
        print(f"   Campaign : {engine.campaign_name}")
        print(f"   Input    : {engine.input_csv.name}")
        print(f"   DB       : {engine.db_path.name}")
        print("   Jobs survive crashes, reboots, Ctrl+C\n")

        while True:
            time.sleep(30)
            s = engine.status()
            print(f"Pending: {s['pending']:<4} | Completed: {s['completed']:<4} | Total: {s['total']:<4} │ {s['db']}")

    except KeyboardInterrupt:
        print("\nShutting down...")
        engine.stop()
        print("Goodbye!")
    except Exception as e:
        logger.exception("Fatal error")
        raise