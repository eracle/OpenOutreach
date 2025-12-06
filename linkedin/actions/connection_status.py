# linkedin/actions/connection_status.py
import logging
from typing import Dict, Any

from linkedin.navigation.enums import ConnectionStatus
from linkedin.sessions import AccountSessionRegistry, AccountSession

logger = logging.getLogger(__name__)


def get_connection_status(
        session: AccountSession,
        profile: Dict[str, Any],
) -> ConnectionStatus:
    """
    Reliably detects connection status using UI inspection.
    Only trusts degree=1 as CONNECTED. Everything else is verified on the page.
    """
    # Ensure browser is ready (safe to call multiple times)
    session.ensure_browser()

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

    page = session.page

    # 1. Is there a "Pending" button?
    if page.locator('button[aria-label*="Pending"]:visible').count() > 0:
        logger.debug("Found visible 'Pending' button → PENDING")
        return ConnectionStatus.PENDING

    # 2. Is there a 1st-degree badge? (covers rare cases where API lags)
    dist_locator = page.locator('span[class*="distance-badge"]:visible')
    if dist_locator.count() > 0:
        badge_text = dist_locator.first.inner_text().strip()
        logger.debug("Distance badge text: %r", badge_text)
        if badge_text.startswith("1"):
            logger.debug("Distance badge shows 1st → CONNECTED")
            return ConnectionStatus.CONNECTED

    # 3. Is there a "Connect" button?
    invite_btn = page.locator('button[aria-label*="Invite"][aria-label*="to connect"]:visible')
    if invite_btn.count() > 0:
        logger.debug("Found 'Connect' button → NOT_CONNECTED")
        return ConnectionStatus.NOT_CONNECTED

    # 4. Nothing clear → play safe
    logger.debug("No clear connection indicators → UNKNOWN")
    return ConnectionStatus.UNKNOWN


if __name__ == "__main__":
    import sys
    import logging
    from linkedin.sessions import SessionKey
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

    profile = {
        "full_name": "Bill Gates",
        "url": "https://www.linkedin.com/in/williamhgates/",
        "public_identifier": "williamhgates",
    }

    print(f"Checking connection status as @{handle} → {profile['full_name']}")
    print(f"Session key: {key}")

    # Get session and navigate
    session = AccountSessionRegistry.get_or_create_from_path(
        handle=key.handle,
        campaign_name=key.campaign_name,
        csv_path=INPUT_CSV_PATH,
    )
    session.ensure_browser()  # ← ensures page is alive
    search_profile(session, profile)  # ← now takes session

    # Check status
    status = get_connection_status(session, profile)
    print(f"Connection status → {status.value}")