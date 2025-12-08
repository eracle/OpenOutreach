# linkedin/actions/message.py
import logging
from typing import Dict, Any, Optional

from linkedin.actions.connection_status import get_connection_status
from linkedin.actions.search import search_profile
from linkedin.navigation.enums import ConnectionStatus, MessageStatus
from linkedin.navigation.utils import goto_page
from linkedin.sessions.registry import AccountSessionRegistry, SessionKey
from linkedin.templates.renderer import render_template

logger = logging.getLogger(__name__)

LINKEDIN_MESSAGING_URL = "https://www.linkedin.com/messaging/"


def send_follow_up_message(
        key: SessionKey,
        profile: Dict[str, Any],
        *,
        template_file: Optional[str] = None,
        template_type: str = "jinja",
        message: Optional[str] = None,
):
    """Sends a follow-up message to a 1st-degree connection."""
    session = AccountSessionRegistry.get_or_create(
        handle=key.handle,
        campaign_name=key.campaign_name,
        csv_hash=key.csv_hash,
    )
    session.ensure_browser()  # ← ensures page is alive and logged in

    # Navigate to profile
    search_profile(session, profile)

    # Render message if not provided directly
    if message is None and template_file:
        message = render_template(template_file, template_type, profile)
    elif message is None:
        message = ""

    # Send the message
    status = send_message_to_profile(session, profile, message)
    logger.info("Message result → %s → %s", profile.get("full_name", profile["url"]), status.value)


def get_messaging_availability(session: "AccountSession", profile: Dict[str, Any]) -> bool:
    """
    Returns True if we should attempt to send a message.
    We allow messaging if:
      - Definitely CONNECTED
      - Or status is UNKNOWN (better to try than miss a real connection)
    """
    status = get_connection_status(session, profile)

    logger.debug("Messaging availability check → %s → %s", profile.get("full_name", "unknown"), status.value)

    if status == ConnectionStatus.CONNECTED:
        return True

    if status == ConnectionStatus.NOT_CONNECTED:
        logger.info("Messaging blocked → not connected")
    elif status == ConnectionStatus.PENDING:
        logger.info("Messaging blocked → connection still pending")
    elif status == ConnectionStatus.UNKNOWN:
        logger.info("Connection Unknown saving profile for debugging.")

    return False


def _perform_send_message_v1(session: "AccountSession", profile: Dict[str, Any], message: str):
    """Low-level message sending with fallback methods (2025-proof)."""
    session.wait()

    page = session.page

    # Try direct "Message" button
    direct = page.locator('button[aria-label*="Message"]:visible')
    if direct.count() > 0:
        direct.first.click()
        logger.debug("Clicked direct Message button")
    else:
        # Fallback: More → Message
        more = page.locator('button[id$="profile-overflow-action"]:visible').first
        more.click()
        session.wait()
        msg_option = page.locator('div[aria-label$="to message"]:visible').first
        msg_option.click()
        logger.debug("Used More → Message flow")

    session.wait()  # give messaging pane time to load

    input_area = page.locator('div[class*="msg-form__contenteditable"]:visible').first

    # Method 1: fill() — fastest when it works
    try:
        input_area.fill(message, timeout=10000)
        logger.debug("Message typed using fill()")
    except Exception as e:
        logger.debug("fill() failed, falling back to clipboard paste: %s", e)
        input_area.click()
        page.evaluate(f"() => navigator.clipboard.writeText(`{message.replace('`', '\\`')}`)")
        session.wait()
        input_area.press("ControlOrMeta+V")
        session.wait()

    # Send
    send_btn = page.locator('button[type="submit"][class*="msg-form"]:visible').first
    send_btn.click(force=True)
    session.wait()
    logger.info("Message sent successfully")


def _send_message(session: "AccountSession", profile: Dict[str, Any], message: str):
    full_name = profile.get("full_name")
    goto_page(
        session,
        action=lambda: session.page.goto(LINKEDIN_MESSAGING_URL),
        expected_url_pattern="/messaging",
        timeout=30_000,
        error_message="Error opening messaging",
    )
    # Click the element with class="entry-point"
    session.page.locator(".entry-point").click(timeout=10_000)

    # Search person
    session.page.locator('input[class^="msg-connections"]').type(full_name, delay=120)

    selector = 'li[data-view-name="messaging-type-ahead-card"]:first-of-type'

    item = session.page.locator(selector)
    item.wait_for(state="visible")

    # Scroll into view + click (very reliable on LinkedIn)
    item.scroll_into_view_if_needed()
    item.click(delay=200)  # small delay between mousedown/mouseup = very human

    session.page.locator('div[class^="msg-form__contenteditable"]').type(message, delay=120)
    session.page.locator('div[class^="class="msg-form__send-button"]').press("Enter")


def send_message_to_profile(
        session: "AccountSession",
        profile: Dict[str, Any],
        message: str,
) -> MessageStatus:
    if not get_messaging_availability(session, profile):
        logger.info("Skipping message → not connected or pending")
        return MessageStatus.SKIPPED

    _perform_send_message_v1(session, profile, message)
    # _send_message(session, profile, message)
    return MessageStatus.SENT


if __name__ == "__main__":
    import sys
    from linkedin.sessions.registry import SessionKey
    from linkedin.campaigns.connect_follow_up import INPUT_CSV_PATH

    logging.getLogger().handlers.clear()
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s │ %(levelname)-8s │ %(message)s',
        datefmt="%H:%M:%S",
    )

    if len(sys.argv) != 2:
        print("Usage: python -m linkedin.actions.message <handle>")
        sys.exit(1)

    handle = sys.argv[1]

    key = SessionKey.make(
        handle=handle,
        campaign_name="test_message",
        csv_path=INPUT_CSV_PATH,
    )

    session = AccountSessionRegistry.get_or_create_from_path(
        handle=handle,
        campaign_name="test_message",
        csv_path=INPUT_CSV_PATH,
    )
    session.ensure_browser()

    test_profile = {
        "full_name": "Bill Gates",
        "url": "https://www.linkedin.com/in/williamhgates/",
        "public_identifier": "williamhgates",
    }

    send_follow_up_message(
        key=key,
        profile=test_profile,
        template_file="./assets/templates/messages/followup.j2",
    )
