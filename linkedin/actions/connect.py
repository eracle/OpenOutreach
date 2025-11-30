# linkedin/actions/connect.py
import logging
from typing import Optional, Dict, Any

from linkedin.sessions import AccountSessionRegistry, SessionKey
from linkedin.navigation.enums import ConnectionStatus
from linkedin.navigation.utils import wait
from linkedin.templates.renderer import render_template

logger = logging.getLogger(__name__)


def send_connection_request(
    key: SessionKey,
    profile: Dict[str, Any],
    template_file: Optional[str] = None,
    template_type: str = "jinja",
) -> ConnectionStatus:

    from linkedin.actions.search import search_profile
    from linkedin.actions.connections import get_connection_status

    session = AccountSessionRegistry.get_or_create(
        handle=key.handle,
        campaign_name=key.campaign_name,
        csv_hash=key.csv_hash,
    )

    resources = session.resources

    logger.debug("1. Navigating to profile: %s", profile.get("linkedin_url"))
    search_profile(session, profile)

    message = render_template(template_file, template_type, profile) if template_file else ""
    logger.debug("2. Rendered note (%d chars): %r", len(message), message.strip()[:200])

    logger.debug("3. Checking current connection status...")
    status = get_connection_status(session, profile)
    logger.info("Current status → %s", status.value)

    skip_reasons = {
        ConnectionStatus.CONNECTED: "Already connected",
        ConnectionStatus.PENDING: "Invitation already pending",
        ConnectionStatus.UNKNOWN: "Unknown status – playing safe",
    }

    if status in skip_reasons:
        name = profile.get('full_name', profile['linkedin_url'])
        logger.info("Skipping send → %s (%s)", name, skip_reasons[status])
        return status

    # 4. Send the invitation
    try:
        _perform_send_invitation(resources, message.strip())
        name = profile.get('full_name') or profile['linkedin_url']
        logger.info("Connection request sent → %s", name)
        return ConnectionStatus.PENDING
    except Exception as e:
        logger.warning("Failed to send invite to %s → %s", profile.get('linkedin_url'), e)
        return ConnectionStatus.UNKNOWN


def _perform_send_invitation(resources, message: str):
    """Low-level click logic – unchanged."""
    wait(resources)

    # Primary: Direct "Connect" button
    direct = resources.page.locator('button[aria-label*="Invite"][aria-label*="to connect"]:visible')
    if direct.count() > 0:
        direct.first.click()
        logger.debug("Clicked direct 'Connect' button")
    else:
        # Fallback: More → Connect
        more = resources.page.locator(
            'button[id*="overflow"]:visible, button[aria-label*="More actions"]:visible').first
        more.click()
        wait(resources)
        connect_option = resources.page.locator(
            'div[role="menuitem"]:has-text("Connect"), span:has-text("Connect")').first
        connect_option.click()
        logger.debug("Used 'More → Connect' flow")

    wait(resources)

    if not message:
        # Send without note
        send_btn = resources.page.locator('button:has-text("Send now"), button[aria-label*="Send without"]')
        logger.debug("Sending without note")
    else:
        # Add note flow
        add_note = resources.page.locator('button:has-text("Add a note"), button[aria-label*="Add a note"]')
        add_note.first.click()
        wait(resources)

        textarea = resources.page.locator('textarea#custom-message, textarea[name="message"]')
        textarea.first.fill(message)
        wait(resources)
        logger.debug("Filled custom note (%d chars)", len(message))

        send_btn = resources.page.locator('button:has-text("Send"), button[aria-label*="Send invitation"]')

    send_btn.first.click(force=True)
    wait(resources)
    logger.debug("Connection request submitted")


# ===================================================================
# Minimal __main__ – now super clean
# ===================================================================
if __name__ == "__main__":
    import sys
    from pathlib import Path
    from linkedin.sessions import SessionKey

    if len(sys.argv) != 2:
        print("Usage: python -m linkedin.actions.connect <handle>")
        sys.exit(1)

    handle = sys.argv[1]
    campaign_name = "test_connect"
    csv_path = Path("debug_input.csv")

    # Build the key once – this is all you need
    key = SessionKey.make(handle, campaign_name, csv_path)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    test_profile = {
        "full_name": "Bill Gates",
        "linkedin_url": "https://www.linkedin.com/in/williamhgates/",
        "public_id": "williamhgates",
    }

    print(f"Testing connection request as @{handle} (session: {key})")
    status = send_connection_request(
        key=key,
        profile=test_profile,
        template_file="./assets/templates/connect_notes/leader.j2",
    )

    print(f"Finished → Status: {status.value}")