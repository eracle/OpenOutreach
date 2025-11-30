# linkedin/acts.py
import logging
from pathlib import Path
from typing import Dict, Any

from linkedin.account_session import AccountSessionRegistry

logger = logging.getLogger(__name__)


def enrich_profile(
        profile_url: str,
        handle: str,
        campaign_name: str,
        csv_hash: str,
        input_csv: Path | None = None,
) -> Dict[str, Any]:
    """
    Synchronously enriches a LinkedIn profile.
    """
    session = AccountSessionRegistry.get_or_create(
        handle=handle,
        campaign_name=campaign_name,
        csv_hash=csv_hash,
        input_csv=input_csv,
    )

    logger.info("ENRICH → %s | session: %s", profile_url[-60:], session.handle)

    # ← Your real Playwright enrichment code goes here
    # await → replace with sync calls or session.page.goto(...).wait_for_load_state()
    # For now: fake delay
    import time
    time.sleep(2)

    logger.info("ENRICH DONE → %s", profile_url[-60:])

    return {
        "profile": {
            "profile_url": profile_url,
            "full_name": "Elon Musk",
            "headline": "Technoking of Tesla",
            "company": "Tesla",
        },
        "raw": {},
    }


def send_connection_request(
        config: dict,
        handle: str,
        campaign_name: str,
        csv_hash: str,
        input_csv: Path | None = None,
):
    """
    Sends a connection request with personalized via Jinja template.
    """
    session = AccountSessionRegistry.get_or_create(
        handle=handle,
        campaign_name=campaign_name,
        csv_hash=csv_hash,
        input_csv=input_csv,
    )

    name = config["context"]["profile"]["full_name"]
    logger.info("CONNECT → %s | session: %s", name, session.handle)

    # ← Your real code: fill note, click "Connect", etc.
    import time
    time.sleep(3)

    logger.info("CONNECT SENT → %s", name)


def is_connection_accepted(
        profile_data: dict,
        handle: str,
        campaign_name: str,
        csv_hash: str,
        input_csv: Path | None = None,
) -> bool:
    """
    Returns True if we're already connected to this person.
    Called once per run — no polling.
    """
    session = AccountSessionRegistry.get_or_create(
        handle=handle,
        campaign_name=campaign_name,
        csv_hash=csv_hash,
        input_csv=input_csv,
    )

    url = profile_data["profile_url"]
    logger.info("CHECK ACCEPT → %s | session: %s", url[-60:], session.handle)

    # ← Replace with real check: go to profile → look for "Message" button instead of "Connect"
    import time
    time.sleep(2)

    # Fake logic — remove when you add real detection
    if "elon" in url.lower() or "william" in url.lower():
        logger.info("ACCEPTED → %s", url[-60:])
        return True

    logger.info("PENDING → not connected yet | %s", url[-60:])
    return False


def send_follow_up_message(
        config: dict,
        handle: str,
        campaign_name: str,
        csv_hash: str,
        input_csv: Path | None = None,
):
    """
    Sends the AI-generated follow-up message after connection is accepted.
    """

    session = AccountSessionRegistry.get_or_create(
        handle=handle,
        campaign_name=campaign_name,
        csv_hash=csv_hash,
        input_csv=input_csv,
    )

    name = config["context"]["profile"]["full_name"]
    logger.info("FOLLOW-UP → %s | session: %s", name, session.handle)

    # ← Your real messaging code here
    import time
    time.sleep(3)

    logger.info("FOLLOW-UP SENT → %s | COMPLETE!", name)
