# linkedin/actions/connections.py
from pathlib import Path
from typing import Dict, Any

from linkedin.navigation.enums import ConnectionStatus


# ← ONLY change: automation instead of resources/context
def get_connection_status(
        automation: "LinkedInAutomation",
        profile: Dict[str, Any],
) -> ConnectionStatus:
    """Checks the connection status of a LinkedIn profile."""
    resources = automation.browser  # ← changed

    # 1. Pending
    if resources.page.locator('button[aria-label*="Pending"]:visible').count() > 0:
        return ConnectionStatus.PENDING

    # 2. Already connected – distance badge starts with "1"
    dist_locator = resources.page.locator('span[class^="distance-badge"]:visible')
    if dist_locator.count() > 0:
        badge_text = dist_locator.first.inner_text().strip()
        if badge_text.startswith("1"):
            return ConnectionStatus.CONNECTED

    # 3. Can send a request
    if resources.page.locator('button[aria-label*="Invite"]:visible').count() > 0:
        return ConnectionStatus.NOT_CONNECTED

    # 4. Fallback
    return ConnectionStatus.UNKNOWN


if __name__ == "__main__":
    import sys
    import logging
    from linkedin.activities.search import search_profile

    from linkedin.account_session import AccountSessionRegistry

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) != 2:
        print("Usage: python -m linkedin.actions.connections <handle>")
        sys.exit(1)

    handle = sys.argv[1]

    automation = AccountSessionRegistry.get_or_create(
        handle=handle,
        campaign_name="test_status",
        csv_hash="debug",
        input_csv=Path("dummy.csv"),
    )

    profile = {
        "full_name": "Bill Gates",
         "linkedin_url": "https://www.linkedin.com/in/ylenia-chiarvesio-59122844/",
        #"linkedin_url": "https://www.linkedin.com/in/williamhgates/",
        "public_id": "williamhgates",
    }

    search_profile(automation, profile)
    status = get_connection_status(automation, profile)
    print(f"Connection status → {status.value}")
