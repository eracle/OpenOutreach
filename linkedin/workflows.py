import time
from datetime import datetime, timedelta
from importlib import import_module
from typing import Callable

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler

from . import campaign_parser
from .database import db_manager


def get_function(function_path: str) -> Callable:
    """Dynamically imports a function from a string path."""
    try:
        module_path, function_name = function_path.rsplit('.', 1)
        module = import_module(module_path)
        return getattr(module, function_name)
    except (ImportError, AttributeError) as e:
        raise ImportError(f"Could not import function '{function_path}': {e}")


# --- Global Instances ---
SCHEDULER: BackgroundScheduler = None


# --- Workflow Functions ---

def start_workflow(linkedin_url: str, campaign: campaign_parser.Campaign, start_step_index: int = 0):
    """Initiates the first step of a campaign for a given URL."""
    if linkedin_url:
        print(f"\nStarting workflow for URL '{linkedin_url}' with campaign '{campaign.campaign_name}'")
    else:
        print(f"\nStarting campaign '{campaign.campaign_name}'")
    execute_step(linkedin_url, campaign, start_step_index)


def execute_step(linkedin_url: str, campaign: campaign_parser.Campaign, step_index: int):
    """Executes a single step of the campaign."""
    if step_index >= len(campaign.steps):
        if linkedin_url:
            print(f"Workflow for {linkedin_url} completed.")
        return

    step = campaign.steps[step_index]

    try:
        func = get_function(step.action)
    except ImportError as e:
        print(f"Error: {e}. Skipping step.")
        execute_step(linkedin_url, campaign, step_index + 1)
        return

    if "read_urls" in step.action:
        profile_urls = func(None, step.model_dump())
        if not profile_urls:
            print("No profile URLs found in the CSV file. Exiting.")
            return
        for url in profile_urls:
            start_workflow(url, campaign, step_index + 1)
        return

    if step.step_type == 'action':
        func(linkedin_url, step.model_dump())
        execute_step(linkedin_url, campaign, step_index + 1)

    elif step.step_type == 'condition':
        print(f"Executing step: {step.action}")
        # Create a unique job ID for rescheduling
        job_id = f"check_{linkedin_url.replace('/', '_').replace(':', '')}_{step_index}"

        SCHEDULER.add_job(
            check_condition_and_proceed_job,
            trigger='date',
            run_date=datetime.now() + timedelta(seconds=5),  # Check after 5 seconds initially
            kwargs={
                'linkedin_url': linkedin_url,
                'campaign': campaign,
                'step_index': step_index
            },
            id=job_id,
            replace_existing=True  # Ensure only one job for this step/url exists
        )
    else:
        print(f"Warning: Unknown step type '{step.step_type}' for {linkedin_url}. Skipping.")
        execute_step(linkedin_url, campaign, step_index + 1)


def check_condition_and_proceed(linkedin_url: str, campaign: campaign_parser.Campaign, step_index: int):
    """The logic that periodically checks a condition."""
    step = campaign.steps[step_index]
    condition_func = get_function(step.action)

    if condition_func(linkedin_url):
        print(f"Condition '{step.action}' met for {linkedin_url}. Proceeding to next step.")
        execute_step(linkedin_url, campaign, step_index + 1)
    else:
        job_id = f"check_{linkedin_url.replace('/', '_').replace(':', '')}_{step_index}"
        job = SCHEDULER.get_job(job_id)

        # TODO: Implement robust timeout logic based on campaign settings
        # For now, just reschedule
        reschedule_time = datetime.now() + timedelta(seconds=15)  # Reschedule after 15 seconds
        SCHEDULER.reschedule_job(job_id, trigger='date', run_date=reschedule_time)
        print(f"Rescheduled check for {linkedin_url} at {reschedule_time.isoformat()}")


# --- Scheduler Callback ---

def check_condition_and_proceed_job(linkedin_url: str, campaign: campaign_parser.Campaign, step_index: int):
    """Standalone function for APScheduler to call, avoiding serialization issues."""
    check_condition_and_proceed(linkedin_url, campaign, step_index)


# --- Main Execution ---

def main(db_url: str = "sqlite:///linkedin.db"):
    """Main function to set up and run the scheduler and workflows."""
    global SCHEDULER

    print("Initializing database...")
    db_manager.init_db(db_url)
    db_manager.create_tables()

    print("Initializing scheduler...")
    jobstores = {'default': SQLAlchemyJobStore(url=db_url)}
    executors = {'default': ThreadPoolExecutor(5)}
    SCHEDULER = BackgroundScheduler(jobstores=jobstores, executors=executors)
    SCHEDULER.start()

    print("Loading campaigns...")
    try:
        campaigns = campaign_parser.load_campaigns()
        if not campaigns:
            print("No campaigns found. Exiting.")
            return
    except ValueError as e:
        print(f"Could not load campaigns: {e}")
        return

    campaign_to_run = campaigns[0]

    # Start the campaign. The 'read_urls' step will trigger the workflows for each URL.
    start_workflow(None, campaign_to_run)

    print("\nScheduler is running. Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        print("Shutting down scheduler...")
        SCHEDULER.shutdown()
        print("Shutdown complete.")


if __name__ == "__main__":
    main()
