# workflows/linked_in_connect_follow_up.py
from datetime import timedelta
from typing import Any, Dict
from temporalio import workflow

from linkedin.account_session import AccountSession

# ======================================================================
# EXACT YAML CONFIGURATION — UPPERCASE CONSTANTS
# ======================================================================
CAMPAIGN_NAME = "linked_in_connect_follow_up"

# Step 2
CONNECT_TEMPLATE_FILE = "./assets/templates/connect_notes/leader.j2"
CONNECT_TEMPLATE_TYPE = "jinja"

# Step 3
WAIT_CHECK_INTERVAL = timedelta(hours=6)

# Step 4
FOLLOWUP_TEMPLATE_FILE = "./assets/templates/prompts/followup_prompt.j2"
FOLLOWUP_TEMPLATE_TYPE = "ai_prompt"

# Shared activity options
ACTIVITY_OPTS = {
    "start_to_close_timeout": timedelta(minutes=30),
    "heartbeat_timeout": timedelta(seconds=60),
    "retry_policy": {
        "maximum_attempts": 3,
        "initial_interval": timedelta(seconds=30),
        "maximum_interval": timedelta(minutes=10),
    },
}

# ======================================================================
# Workflow — receives AccountSession from caller
# ======================================================================
@workflow.defn(name=CAMPAIGN_NAME)
class LinkedInConnectFollowUpWorkflow:
    def __init__(self, session: AccountSession) -> None:
        self.session = session

    @workflow.run
    async def run(self, profile_url: str) -> Dict[str, Any]:

        # ==================================================================
        # STEP 1 → enrich_profile (config: {})
        # ==================================================================
        enriched = await workflow.execute_activity(
            "enrich_profile",
            profile_url,
            args=[self.session],
            **ACTIVITY_OPTS,
        )
        # enriched now contains at least: {"profile": "...", "full_name": "...", ...}

        # ==================================================================
        # STEP 2 → send_connection_request
        # ==================================================================
        await workflow.execute_activity(
            "send_connection_request",
            {
                "template_file": CONNECT_TEMPLATE_FILE,
                "template_type": CONNECT_TEMPLATE_TYPE,
                "context": enriched,
            },
            args=[self.session],
            **ACTIVITY_OPTS,
        )

        # ==================================================================
        # STEP 3 → wait_for_acceptance (polite & undetectable)
        # ==================================================================
        while True:
            accepted = await workflow.execute_activity(
                "is_connection_accepted",
                enriched["profile"],
                args=[self.session],
                start_to_close_timeout=timedelta(minutes=5),
                **ACTIVITY_OPTS,
            )

            if accepted:
                break

            # Sleep 6 hours, fully replay-safe, zero resource usage
            await workflow.sleep(WAIT_CHECK_INTERVAL)

        # ==================================================================
        # STEP 4 → send_follow_up_message
        # ==================================================================
        await workflow.execute_activity(
            "send_follow_up_message",
            {
                "template_file": FOLLOWUP_TEMPLATE_FILE,
                "template_type": FOLLOWUP_TEMPLATE_TYPE,
                "context": enriched,
            },
            args=[self.session],
            **ACTIVITY_OPTS,
        )

        return {"status": "completed", "enriched": enriched}