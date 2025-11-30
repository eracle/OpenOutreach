# campaigns/connect_follow_up.py
import logging
from pathlib import Path
from typing import Dict, Any



logger = logging.getLogger(__name__)

# ———————————————————————————————— USER CONFIGURATION ————————————————————————————————
# ←←← Edit these two values at the top of the file whenever you start a new campaign ←←←

CAMPAIGN_NAME = "connect_follow_up"  # Change for each campaign
INPUT_CSV_PATH = Path("./assets/inputs/urls.csv")

# ———————————————————————————————— Template Config ————————————————————————————————
CONNECT_TEMPLATE_FILE = "./assets/templates/connect_notes/leader.j2"
CONNECT_TEMPLATE_TYPE = "jinja"

FOLLOWUP_TEMPLATE_FILE = "./assets/templates/prompts/followup_prompt.j2"
FOLLOWUP_TEMPLATE_TYPE = "ai_prompt"


# ———————————————————————————————— Core Logic ————————————————————————————————
def process_profile_row(
        profile_url: str,
        handle: str,
        campaign_name: str,
        csv_hash: str,
) -> Dict[str, Any]:
    from linkedin.acts import (
        enrich_profile,
        send_connection_request,
        is_connection_accepted,
        send_follow_up_message,
    )
    logger.info(f"Processing → {handle} | {profile_url}")

    enriched = enrich_profile(profile_url, handle, campaign_name, csv_hash)

    send_connection_request(
        {
            "template_file": CONNECT_TEMPLATE_FILE,
            "template_type": CONNECT_TEMPLATE_TYPE,
            "context": enriched,
        },
        handle,
        campaign_name,
        csv_hash,
    )

    # Instant check – no waiting loop
    already_connected = is_connection_accepted(
        enriched["profile"], handle, campaign_name, csv_hash
    )

    if not already_connected:
        logger.info(f"Connection request sent, waiting for acceptance later → {handle}")
        return {
            "handle": handle,
            "status": "waiting_for_acceptance",
            "action": "connection_request_sent",
        }

    # Already connected → send follow-up immediately
    logger.info(f"Already connected → sending follow-up message to {handle}")
    send_follow_up_message(
        {
            "template_file": FOLLOWUP_TEMPLATE_FILE,
            "template_type": FOLLOWUP_TEMPLATE_TYPE,
            "context": enriched,
        },
        handle,
        campaign_name,
        csv_hash,
    )

    return {
        "handle": handle,
        "status": "completed",
        "action": "follow_up_sent",
    }
