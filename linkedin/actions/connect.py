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
    status = get_connection_status(session, profile)  # now takes session
    logger.info("Current status → %s", status.value)

    skip_reasons = {
        ConnectionStatus.CONNECTED: "Already connected",
        ConnectionStatus.PENDING: "Invitation already pending",
        ConnectionStatus.UNKNOWN: "Unknown status – playing safe",
    }

    if status in skip_reasons:
        name = profile.get('full_name', profile.get('url'))
        logger.info("Skipping send → %s (%s)", name, skip_reasons[status])
        return status

    # 4. Send invitation WITHOUT note (current active flow)
    _perform_send_invitation_without_note(session)

    name = profile.get('full_name') or profile.get('url')
    logger.info("Connection request sent (no note) → %s", name)
    return ConnectionStatus.PENDING


def _perform_send_invitation_without_note(session):
    """Click flow: sends connection request instantly without note."""
    wait(session)
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
        wait(session)
        connect_option = top_card.locator(
            'div[role="button"][aria-label^="Invite"][aria-label*=" to connect"]'
        ).first
        connect_option.click()
        logger.debug("Used 'More → Connect' flow")

    wait(session)

    # Click "Send now" / "Send without a note"
    send_btn = session.page.locator(
        'button:has-text("Send now"), '
        'button[aria-label*="Send without"], '
        'button[aria-label*="Send invitation"]:not([aria-label*="note"])'
    )
    send_btn.first.click(force=True)
    wait(session)
    logger.debug("Connection request submitted (no note)")


# ===================================================================
# FUTURE: Send with personalized note (just uncomment when ready)
# ===================================================================
def _perform_send_invitation_with_note(session, message: str):
    """Full flow with custom note – ready to enable anytime."""
    wait(session)
    top_card = session.page.locator('section:has(div.top-card-background-hero-image)')

    direct = top_card.locator('button[aria-label*="Invite"][aria-label*="to connect"]:visible')
    if direct.count() > 0:
        direct.first.click()
    else:
        more = top_card.locator('button[id*="overflow"], button[aria-label*="More actions"]').first
        more.click()
        wait(session)
        session.page.locator('div[role="button"][aria-label^="Invite"][aria-label*=" to connect"]').first.click()

    wait(session)
    session.page.locator('button:has-text("Add a note")').first.click()
    wait(session)

    textarea = session.page.locator('textarea#custom-message, textarea[name="message"]')
    textarea.first.fill(message)
    wait(session)
    logger.debug("Filled note (%d chars)", len(message))

    session.page.locator('button:has-text("Send"), button[aria-label*="Send invitation"]').first.click(force=True)
    wait(session)
    logger.debug("Connection request with note sent")


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
        "full_name": "Lex Fridman",
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
