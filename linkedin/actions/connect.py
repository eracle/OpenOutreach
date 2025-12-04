# linkedin/actions/connect.py
import logging
from typing import Optional, Dict, Any

from linkedin.navigation.enums import ConnectionStatus
from linkedin.navigation.utils import wait
from linkedin.sessions import AccountSessionRegistry, SessionKey

logger = logging.getLogger(__name__)


def send_connection_request(
        key: SessionKey,
        profile: Dict[str, Any],
        template_file: Optional[str] = None,  # ← kept for future use
        template_type: str = "jinja",  # ← kept for future use
) -> ConnectionStatus:
    """
    Sends a LinkedIn connection request WITHOUT a note (fastest & least restricted).
    All note-sending logic has been moved to a separate unused function for future use.
    """
    from linkedin.actions.search import search_profile
    from linkedin.actions.connection_status import get_connection_status

    session = AccountSessionRegistry.get_or_create(
        handle=key.handle,
        campaign_name=key.campaign_name,
        csv_hash=key.csv_hash,
    )

    resources = session.resources

    logger.debug("1. Navigating to profile: %s", profile.get("url"))
    search_profile(session, profile)

    # ← Note rendering is preserved but currently ignored (kept for future reactivation)
    if template_file:
        from linkedin.templates.renderer import render_template
        message = render_template(template_file, template_type, profile)
        logger.debug("Rendered note (%d chars): %r", len(message), message.strip()[:200])
    else:
        message = ""

    logger.debug("3. Checking current connection status...")
    status = get_connection_status(session, profile)
    logger.info("Current status → %s", status.value)

    skip_reasons = {
        ConnectionStatus.CONNECTED: "Already connected",
        ConnectionStatus.PENDING: "Invitation already pending",
        ConnectionStatus.UNKNOWN: "Unknown status – playing safe",
    }

    if status in skip_reasons:
        name = profile.get('full_name', profile['url'])
        logger.info("Skipping send → %s (%s)", name, skip_reasons[status])
        return status

    # 4. Send invitation WITHOUT note (message-free flow)
    _perform_send_invitation_without_note(resources)

    name = profile.get('full_name') or profile['url']
    logger.info("Connection request sent (no note) → %s", name)
    return ConnectionStatus.PENDING


def _perform_send_invitation_without_note(resources):
    """Current active flow: sends invitation instantly without any note."""
    wait(resources)
    top_card_section = resources.page.locator('section:has(div.top-card-background-hero-image)')

    # Primary: Direct "Connect" button
    direct = top_card_section.locator('button[aria-label*="Invite"][aria-label*="to connect"]:visible')
    if direct.count() > 0:
        direct.first.click()
        logger.debug("Clicked direct 'Connect' button")
    else:
        # Fallback: More → Connect
        more = top_card_section.locator(
            'button[id*="overflow"]:visible, '
            'button[aria-label*="More actions"]:visible'
        ).first
        more.click()
        wait(resources)
        connect_option = top_card_section.locator(
            'div[role="button"][aria-label^="Invite"][aria-label*=" to connect"]'
        ).first
        connect_option.click()
        logger.debug("Used 'More → Connect' flow")

    wait(resources)

    # Click "Send now" / "Send without note"
    send_btn = resources.page.locator(
        'button:has-text("Send now"), '
        'button[aria-label*="Send without"], '
        'button[aria-label*="Send invitation"]:not([aria-label*="note"])'
    )
    send_btn.first.click(force=True)
    wait(resources)
    logger.debug("Connection request submitted (no note)")


# ===================================================================
# FUTURE USE: Full invitation with personalized note
# ===================================================================
# Uncomment and replace the call in send_connection_request() when you're ready
# to start sending notes again.
def _perform_send_invitation_with_note(resources, message: str):
    """Low-level click logic for sending invitation WITH a custom note."""
    wait(resources)
    top_card_section = resources.page.locator('section:has(div.top-card-background-hero-image)')

    # Primary: Direct "Connect" button
    direct = top_card_section.locator('button[aria-label*="Invite"][aria-label*="to connect"]:visible')
    if direct.count() > 0:
        direct.first.click()
        logger.debug("Clicked direct 'Connect' button")
    else:
        # Fallback: More → Connect
        more = top_card_section.locator(
            'button[id*="overflow"]:visible, button[aria-label*="More actions"]:visible').first
        more.click()
        wait(resources)
        connect_option = top_card_section.locator(
            'div[role="button"][aria-label^="Invite"][aria-label*=" to connect"]').first
        connect_option.click()
        logger.debug("Used 'More → Connect' flow")

    wait(resources)

    # Always go through "Add a note"
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
    logger.debug("Connection request with note submitted")


if __name__ == "__main__":
    import sys
    from linkedin.sessions import SessionKey
    from linkedin.campaigns.connect_follow_up import INPUT_CSV_PATH

    if len(sys.argv) != 2:
        print("Usage: python -m linkedin.actions.connect <handle>")
        sys.exit(1)

    handle = sys.argv[1]
    key = SessionKey.make(handle, "test_connect", INPUT_CSV_PATH)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    public_identifier = "lexfridman"
    test_profile = {
        "url": f"https://www.linkedin.com/in/{public_identifier}/",
        "public_identifier": public_identifier,
    }

    print(f"Testing connection request as @{handle} (session: {key})")
    status = send_connection_request(
        key=key,
        profile=test_profile,
        template_file="./assets/templates/connect_notes/leader.j2",  # still works, just ignored for now
    )

    print(f"Finished → Status: {status.value}")
