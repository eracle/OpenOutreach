# linkedin/actions/connection_status.py
import logging
from typing import Dict, Any

from linkedin.actions.search import search_profile
from linkedin.navigation.enums import ProfileState
from linkedin.navigation.utils import get_top_card

logger = logging.getLogger(__name__)

SELECTORS = {
    "pending_button": 'button[aria-label*="Pending"]:visible',
    "invite_to_connect": 'button[aria-label*="Invite"][aria-label*="to connect"]:visible',
}


def get_connection_status(
        session: "AccountSession",
        profile: Dict[str, Any],
) -> ProfileState:
    """
    Reliably detects connection status using UI inspection.
    Only trusts degree=1 as CONNECTED. Everything else is verified on the page.
    """
    # Ensure browser is ready (safe to call multiple times)
    session.ensure_browser()
    search_profile(session, profile)
    session.wait()

    logger.debug("Checking connection status → %s", profile.get("public_identifier"))

    degree = profile.get("connection_degree", None)

    # Fast path: API says 1st degree → trust it
    if degree == 1:
        logger.debug("API reports 1st degree → instantly trusted as CONNECTED")
        return ProfileState.CONNECTED

    logger.debug("connection_degree=%s → API unreliable, switching to UI inspection", degree or "None")

    top_card = get_top_card(session)

    # 1. Pending invitation?
    if top_card.locator(SELECTORS["pending_button"]).count() > 0:
        logger.debug("Detected 'Pending' button → PENDING")
        return ProfileState.PENDING

    main_text = top_card.inner_text()
    # 1b. Is there a "Pending" label?
    if any(x in main_text for x in ["Pending"]):
        logger.debug("Detected 'Pending' text in page → PENDING")
        return ProfileState.PENDING

    # 2. Already connected?
    if any(x in main_text for x in ["1st", "1st degree", "1º", "1er"]):
        logger.debug("Confirmed 1st degree via page text → CONNECTED")
        return ProfileState.CONNECTED

    # 3a. Connect button visible?
    invite_btn = top_card.locator(SELECTORS["invite_to_connect"])
    if invite_btn.count() > 0:
        logger.debug("Found 'Connect' button → NOT_CONNECTED")
        return ProfileState.ENRICHED

    # 3b. Is there a "Connect" label?
    if any(indicator in main_text for indicator in ["Connect"]):
        logger.debug("Found 'Connect' label in page → NOT_CONNECTED")
        return ProfileState.ENRICHED

    if degree:
        logger.debug("API reports present → NOT CONNECTED")
        return ProfileState.ENRICHED

    # 4. Ambiguous → default safe
    logger.debug("No clear indicators → defaulting to NOT_CONNECTED")
    # save_page(profile, session)  # uncomment if you want HTML dumps
    return ProfileState.ENRICHED


if __name__ == "__main__":
    import sys
    import logging
    from linkedin.sessions.registry import get_session

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if len(sys.argv) != 2:
        print("Usage: python -m linkedin.actions.connections <handle>")
        sys.exit(1)

    handle = sys.argv[1]

    public_identifier = "benjames01"
    test_profile = {
        "full_name": "Ben James",
        "url": f"https://www.linkedin.com/in/{public_identifier}/",
        "public_identifier": public_identifier,
    }

    print(f"Checking connection status as @{handle} → {test_profile['full_name']}")

    # Get session and navigate
    session = get_session(
        handle=handle,
    )

    # Check status
    status = get_connection_status(session, test_profile)
    print(f"Connection status → {status.value}")
