# linkedin/actions/connections.py
import logging  # ← added
from typing import Dict, Any

from linkedin.navigation.enums import ConnectionStatus
from linkedin.sessions import AccountSessionRegistry, AccountSession

logger = logging.getLogger(__name__)


def get_connection_status(
        account_session: AccountSession,
        profile: Dict[str, Any],
) -> ConnectionStatus:
    """
    Checks the current connection status of a profile.
    First tries the reliable connection_degree (int) from Voyager API using a map.
    Falls back to UI inspection only when needed.
    """
    resources = account_session.resources

    logger.debug("Checking connection status for %s", profile.get("full_name", "unknown"))

    # ── Fast path: use connection_degree from Voyager API if present ──
    degree = profile.get("connection_degree")

    # Mapping: connection_degree (int or None) → ConnectionStatus
    degree_to_status = {
        1: ConnectionStatus.CONNECTED,
        2: ConnectionStatus.NOT_CONNECTED,
        3: ConnectionStatus.NOT_CONNECTED,
        None: None,  # explicitly continue to UI checks
    }

    if degree in degree_to_status:
        status = degree_to_status[degree]
        if status is not None:  # i.e. we got a definitive answer
            logger.debug("Connection status from API (degree=%s) → %s", degree, status.value)
            return status
        # else: degree is None → fall through to UI checks
    else:
        logger.debug("No connection_degree in profile data → falling back to UI inspection")

    # ── Fallback: classic UI-based detection (unchanged) ──

    # 1. Pending
    if resources.page.locator('button[aria-label*="Pending"]:visible').count() > 0:
        logger.debug("Found Pending button visible")
        return ConnectionStatus.PENDING

    # 2. Already connected – distance badge starts with "1"
    dist_locator = resources.page.locator('span[class^="distance-badge"]:visible')
    if dist_locator.count() > 0:
        badge_text = dist_locator.first.inner_text().strip()
        logger.debug("Distance badge text: %r", badge_text)
        if badge_text.startswith("1"):
            return ConnectionStatus.CONNECTED

    # 3. Can send a request
    if resources.page.locator('button[aria-label*="Invite"]:visible').count() > 0:
        logger.debug("Invite button visible")
        return ConnectionStatus.NOT_CONNECTED

    # 4. Fallback
    logger.debug("No known indicators found → UNKNOWN")
    return ConnectionStatus.UNKNOWN

if __name__ == "__main__":
    import sys
    import logging
    from linkedin.sessions import SessionKey
    from linkedin.actions.search import search_profile

    from linkedin.campaigns.connect_follow_up import INPUT_CSV_PATH

    logging.basicConfig(
        level=logging.DEBUG,  # Changed to DEBUG for full logs
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

    # Navigate first
    account_session = AccountSessionRegistry.get_or_create_from_path(
        handle=key.handle,
        campaign_name=key.campaign_name,
        csv_path=INPUT_CSV_PATH,
    )
    search_profile(account_session, profile)

    # Then check status
    status = get_connection_status(account_session, profile)
    print(f"Connection status → {status.value}")
