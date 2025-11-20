from typing import Dict, Any

from linkedin.navigation.enums import ConnectionStatus
from linkedin.navigation.login import PlaywrightResources


def is_connection_accepted(context: Dict[str, Any], linkedin_url: str):
    print(f"CONDITION: Checking if connection accepted for {linkedin_url}")


def get_connection_status(
        resources: PlaywrightResources,
        profile: Dict[str, Any],
) -> ConnectionStatus:
    """Checks the connection status of a LinkedIn profile."""
    # 1. Pending – very reliable, text is almost always in English ("Pending") even on localized UIs
    if resources.page.locator('button[aria-label*="Pending"]:visible').count() > 0:
        return ConnectionStatus.PENDING

    # 2. Already connected – distance badge is present and starts with "1"
    #     Works for English ("1st"), Italian/Spanish/Portuguese ("1°"), French ("1er"), etc.
    dist_locator = resources.page.locator('span[class^="distance-badge"]:visible')
    if dist_locator.count() > 0:
        badge_text = dist_locator.first.inner_text().strip()
        if badge_text.startswith("1"):
            return ConnectionStatus.CONNECTED

    # 3. Can send a request – direct "Invite … to connect" button exists
    if resources.page.locator('button[aria-label*="Invite"]:visible').count() > 0:
        return ConnectionStatus.NOT_CONNECTED

    # 4. Fallback
    return ConnectionStatus.UNKNOWN
