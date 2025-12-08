# linkedin/actions/connect.py
import logging
from typing import Optional, Dict, Any

from linkedin.navigation.enums import ConnectionStatus
from linkedin.sessions.registry import AccountSessionRegistry, SessionKey

logger = logging.getLogger(__name__)


def send_connection_request(
        key: SessionKey,
        profile: Dict[str, Any],
        template_file: Optional[str] = None,
        template_type: str = "jinja",
) -> ConnectionStatus:
    """
    Sends a LinkedIn connection request WITHOUT a note (fastest & safest).
    All note-sending logic preserved below for future use.
    """
    from linkedin.actions.search import search_profile
    from linkedin.actions.connection_status import get_connection_status

    # Get the singleton session (auto-recovers browser if crashed)
    session = AccountSessionRegistry.get_or_create(
        handle=key.handle,
        campaign_name=key.campaign_name,
        csv_hash=key.csv_hash,
    )

    logger.debug("1. Navigating to profile: %s", profile.get("url"))
    search_profile(session, profile)  # now takes session, not resources

    # Render note if template provided (currently not sent – kept for future)
    if template_file:
        from linkedin.templates.renderer import render_template
        message = render_template(template_file, template_type, profile)
        logger.debug("Rendered note (%d chars): %r", len(message), message.strip()[:200])
    else:
        message = ""

    logger.debug("3. Checking current connection status...")
    connection_status = get_connection_status(session, profile)  # now takes session
    logger.info("Current status → %s", connection_status.value)

    skip_reasons = {
        ConnectionStatus.CONNECTED: "Already connected",
        ConnectionStatus.PENDING: "Invitation already pending",
        ConnectionStatus.UNKNOWN: "Unknown status – playing safe",
    }

    if connection_status in skip_reasons:
        name = profile.get('full_name', profile.get('url'))
        logger.info("Skipping send → %s (%s)", name, skip_reasons[connection_status])
        return connection_status

    # 4. Send invitation WITHOUT note (current active flow)
    _perform_send_invitation_without_note(session)

    name = profile.get('full_name') or profile.get('url')
    logger.info("Connection request sent (no note) → %s", name)
    return ConnectionStatus.PENDING


def _perform_send_invitation_without_note(session):
    """Click flow: sends connection request instantly without note."""
    session.wait()
    top_card = session.page.locator('section:has(div.top-card-background-hero-image)')

    # Direct "Connect" button
    direct = top_card.locator('button[aria-label*="Invite"][aria-label*="to connect"]:visible')
    if direct.count() > 0:
        direct.first.click()
        logger.debug("Clicked direct 'Connect' button")
    else:
        # Fallback: More → Connect
        more = top_card.locator(
            'button[id*="overflow"]:visible, '
            'button[aria-label*="More actions"]:visible'
        ).first
        more.click()
        session.wait()
        connect_option = top_card.locator(
            'div[role="button"][aria-label^="Invite"][aria-label*=" to connect"]'
        ).first
        connect_option.click()
        logger.debug("Used 'More → Connect' flow")

    session.wait()

    # Click "Send now" / "Send without a note"
    send_btn = session.page.locator(
        'button:has-text("Send now"), '
        'button[aria-label*="Send without"], '
        'button[aria-label*="Send invitation"]:not([aria-label*="note"])'
    )
    send_btn.first.click(force=True)
    session.wait()
    logger.debug("Connection request submitted (no note)")


# ===================================================================
# FUTURE: Send with personalized note (just uncomment when ready)
# ===================================================================
def _perform_send_invitation_with_note(session, message: str):
    """Full flow with custom note – ready to enable anytime."""
    session.wait()
    top_card = session.page.locator('section:has(div.top-card-background-hero-image)')

    direct = top_card.locator('button[aria-label*="Invite"][aria-label*="to connect"]:visible')
    if direct.count() > 0:
        direct.first.click()
    else:
        more = top_card.locator('button[id*="overflow"], button[aria-label*="More actions"]').first
        more.click()
        session.wait()
        session.page.locator('div[role="button"][aria-label^="Invite"][aria-label*=" to connect"]').first.click()

    session.wait()
    session.page.locator('button:has-text("Add a note")').first.click()
    session.wait()

    textarea = session.page.locator('textarea#custom-message, textarea[name="message"]')
    textarea.first.fill(message)
    session.wait()
    logger.debug("Filled note (%d chars)", len(message))

    session.page.locator('button:has-text("Send"), button[aria-label*="Send invitation"]').first.click(force=True)
    session.wait()
    logger.debug("Connection request with note sent")


if __name__ == "__main__":
    import sys
    from linkedin.sessions.registry import SessionKey
    from linkedin.campaigns.connect_follow_up import INPUT_CSV_PATH

    if len(sys.argv) != 2:
        print("Usage: python -m linkedin.actions.connect <handle>")
        sys.exit(1)

    handle = sys.argv[1]
    key = SessionKey.make(handle, "test_connect", INPUT_CSV_PATH)

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    public_identifier = "benjames01"
    test_profile = {
        "full_name": "Ben James",
        "url": f"https://www.linkedin.com/in/{public_identifier}/",
        "public_identifier": public_identifier,
    }

    print(f"Testing connection request as @{handle} (session: {key})")
    status = send_connection_request(
        key=key,
        profile=test_profile,
        template_file="./assets/templates/messages/followup.j2",
    )

    print(f"Finished → Status: {status.value}")
