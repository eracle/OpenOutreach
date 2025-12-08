# linkedin/actions/connection_status.py
import logging
import os
from datetime import datetime
from typing import Dict, Any

from linkedin.conf import FIXTURE_PAGES_DIR
from linkedin.navigation.enums import ConnectionStatus
from linkedin.sessions.registry import AccountSessionRegistry

logger = logging.getLogger(__name__)


def get_connection_status(
        session: "AccountSession",
        profile: Dict[str, Any],
) -> ConnectionStatus:
    """
    Reliably detects connection status using UI inspection.
    Only trusts degree=1 as CONNECTED. Everything else is verified on the page.
    """
    # Ensure browser is ready (safe to call multiple times)
    session.ensure_browser()
    session.wait()
    logger.debug("Checking connection status for %s", profile.get("full_name", "unknown"))

    degree = profile.get("connection_degree")

    # Fast path: API says 1st degree → trust it
    if degree == 1:
        logger.debug("Connection status from API (degree=1) → CONNECTED")
        return ConnectionStatus.CONNECTED

    # For degree=2, 3, or None → API is unreliable (pending invites look identical)
    logger.debug(
        "connection_degree=%s → cannot trust API alone. Falling back to UI inspection.",
        degree
    )

    main_container = session.page.locator('main').first

    # 1a. Is there a "Pending" button?
    if main_container.locator('button[aria-label*="Pending"]:visible').count() > 0:
        logger.debug("Found visible 'Pending' button → PENDING")
        return ConnectionStatus.PENDING

    main_text = main_container.inner_text()
    # 1b. Is there a "Pending" label?
    if any(indicator in main_text for indicator in ["Pending"]):
        logger.debug("Found visible 'Pending' button → PENDING")
        return ConnectionStatus.PENDING

    # 2. Is there a "1st" label?
    if any(indicator in main_text for indicator in ["1st", "1st degree", "1º", "1er"]):
        logger.debug("Confirmed 1st degree connection via <main> text")
        return ConnectionStatus.CONNECTED

    # 3. Is there a "Connect" button?
    invite_btn = main_container.locator('button[aria-label*="Invite"][aria-label*="to connect"]:visible')
    if invite_btn.count() > 0:
        logger.debug("Found 'Connect' button → NOT_CONNECTED")
        return ConnectionStatus.NOT_CONNECTED

    # 4. Nothing clear → play safe + SAVE HTML for debugging
    logger.debug("No clear connection indicators → UNKNOWN")

    filepath = FIXTURE_PAGES_DIR / f"{profile.get("public_identifier")}.html"
    html_content = session.page.content()
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_content)
    logger.info("Saved unknown connection status page → %s", filepath)

    return ConnectionStatus.UNKNOWN


if __name__ == "__main__":
    import sys
    import logging
    from linkedin.sessions.registry import SessionKey
    from linkedin.actions.search import search_profile
    from linkedin.campaigns.connect_follow_up import INPUT_CSV_PATH

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if len(sys.argv) != 2:
        print("Usage: python -m linkedin.actions.connections <handle>")
        sys.exit(1)

    handle = sys.argv[1]
    key = SessionKey.make(
        handle=handle,
        campaign_name="test_status",
        csv_path=INPUT_CSV_PATH,
    )

    public_identifier = "benjames01"
    test_profile = {
        "full_name": "Ben James",
        "url": f"https://www.linkedin.com/in/{public_identifier}/",
        "public_identifier": public_identifier,
    }

    print(f"Checking connection status as @{handle} → {test_profile['full_name']}")
    print(f"Session key: {key}")

    # Get session and navigate
    session = AccountSessionRegistry.get_or_create_from_path(
        handle=key.handle,
        campaign_name=key.campaign_name,
        csv_path=INPUT_CSV_PATH,
    )
    session.ensure_browser()
    search_profile(session, test_profile)

    # Check status
    status = get_connection_status(session, test_profile)
    print(f"Connection status → {status.value}")
