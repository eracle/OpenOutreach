# campaigns/connect_follow_up.py
import logging
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger(__name__)

# ———————————————————————————————— USER CONFIGURATION ————————————————————————————————
CAMPAIGN_NAME = "connect_follow_up"
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
        campaign_name: str = CAMPAIGN_NAME,
) -> Dict[str, Any]:
    from linkedin.sessions import SessionKey
    from linkedin.actions.connect import send_connection_request
    from linkedin.actions.message import send_follow_up_message
    from linkedin.actions.profile import enrich_profile
    from linkedin.navigation.enums import ConnectionStatus

    key = SessionKey.make(
        handle=handle,
        campaign_name=campaign_name,
        csv_path=INPUT_CSV_PATH,
    )

    profile = {"linkedin_url": profile_url}

    logger.info(f"Processing → @{handle} | {profile_url} | SessionKey: {key}")
    logger.debug(f"SessionKey details → handle={key.handle} campaign={key.campaign_name} hash={key.csv_hash}")

    # 1. Enrich
    logger.debug("Enriching profile...")
    enriched, _ = enrich_profile(key=key, profile=profile)
    logger.debug(f"Enriched keys: {list(enriched.keys())}")

    # 2. Send connection request (if needed)
    logger.debug("Sending connection request...")
    status = send_connection_request(
        key=key,
        profile=enriched,
        template_file=CONNECT_TEMPLATE_FILE,
        template_type=CONNECT_TEMPLATE_TYPE,
    )
    logger.info(f"Connection request result → {status.value}")

    # If already connected or pending → send follow-up immediately
    if status == ConnectionStatus.CONNECTED:
        logger.info("Already connected → sending follow-up")
        send_follow_up_message(
            key=key,
            profile=enriched,
            template_file=FOLLOWUP_TEMPLATE_FILE,
            template_type=FOLLOWUP_TEMPLATE_TYPE,
        )
        final_action = "follow_up_sent"
    else:
        logger.info(f"Connection request {status.value.lower()} → will follow-up later")
        final_action = "connection_request_sent"

    return {
        "handle": handle,
        "status": "completed" if final_action == "follow_up_sent" else "waiting_for_acceptance",
        "action": final_action,
    }
