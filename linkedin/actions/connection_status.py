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

    # Check pending button in DOM first (most reliable)
    if top_card.locator(SELECTORS["pending_button"]).count() > 0:
        logger.debug("Detected 'Pending' button → PENDING")
        return ProfileState.PENDING

    main_text = top_card.inner_text()

    # Text-based indicators, checked in priority order
    TEXT_INDICATORS = [
        (["Pending"], ProfileState.PENDING, "Detected 'Pending' text → PENDING"),
        (["1st", "1st degree", "1º", "1er"], ProfileState.CONNECTED, "Confirmed 1st degree via text → CONNECTED"),
    ]
    for keywords, state, msg in TEXT_INDICATORS:
        if any(kw in main_text for kw in keywords):
            logger.debug(msg)
            return state

    # Connect button or label visible → not connected
    if top_card.locator(SELECTORS["invite_to_connect"]).count() > 0:
        logger.debug("Found 'Connect' button → NOT_CONNECTED")
        return ProfileState.NEW

    if "Connect" in main_text or degree:
        logger.debug("Connect label or degree present → NOT_CONNECTED")
        return ProfileState.NEW

    logger.debug("No clear indicators → defaulting to NOT_CONNECTED")
    # save_page(profile, session)  # uncomment if you want HTML dumps
    return ProfileState.NEW


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
