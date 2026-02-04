# linkedin/actions/message.py
import json
import logging
import sys
from typing import Dict, Any

from linkedin.actions.connection_status import get_connection_status
from linkedin.navigation.enums import ProfileState, MessageStatus
from linkedin.navigation.utils import goto_page
from linkedin.sessions.registry import get_session
from linkedin.templates.renderer import render_template

logger = logging.getLogger(__name__)

LINKEDIN_MESSAGING_URL = "https://www.linkedin.com/messaging/thread/new/"


def send_follow_up_message(
        handle: str,
        profile: Dict[str, Any],
) -> tuple:
    """
    Send a follow-up message to a connected profile.

    Returns:
        Tuple of (MessageStatus, message_content or None)
    """
    session = get_session(
        handle=handle,
    )
    template_file = session.account_cfg["followup_template"]
    template_type = session.account_cfg["followup_template_type"]

    status = get_connection_status(session, profile)

    public_identifier = profile.get("public_identifier")
    logger.debug(f"Messaging check → {public_identifier} → {status.value}")

    if status != ProfileState.CONNECTED:
        logger.info(f"Message skipped → not connected with {public_identifier}")
        return MessageStatus.SKIPPED, None
    else:
        message = None
        if template_file:
            message = render_template(session, template_file, template_type, profile)

        s1 = _send_msg_pop_up(session, profile, message)
        s2 = s1 or _send_message(session, profile, message)
        success = s2
        logger.info(f"Message sent: {message}") if success else None

        if success:
            return MessageStatus.SENT, message
        else:
            return MessageStatus.SKIPPED, None


def _send_msg_pop_up(session: "AccountSession", profile: Dict[str, Any], message: str) -> bool:
    session.wait()
    page = session.page
    public_identifier = profile.get("public_identifier")

    try:
        direct = page.locator('button[aria-label*="Message"]:visible')
        if direct.count() > 0:
            direct.first.click()
            logger.debug("Opened Message popup (direct button)")
        else:
            more = page.locator('button[id$="profile-overflow-action"]:visible').first
            more.click()
            session.wait()
            msg_option = page.locator('div[aria-label$="to message"]:visible').first
            msg_option.click()
            logger.debug("Opened Message via More → Message")

        session.wait()

        input_area = page.locator('div[class*="msg-form__contenteditable"]:visible').first

        try:
            input_area.fill(message, timeout=10000)
            logger.debug("Message typed cleanly")
        except Exception:
            logger.debug("fill() failed → using clipboard paste")
            input_area.click()
            page.evaluate(f"() => navigator.clipboard.writeText({json.dumps(message)})")
            session.wait()
            input_area.press("ControlOrMeta+V")
            session.wait()

        send_btn = page.locator('button[type="submit"][class*="msg-form"]:visible').first
        send_btn.click(force=True)
        session.wait(4, 5)

        page.keyboard.press("Escape")
        session.wait()

        logger.info("Message sent to %s", public_identifier)
        return True

    except Exception as e:
        logger.error("Failed to send message to %s → %s", public_identifier, e)
        return False


def _send_message(session: "AccountSession", profile: Dict[str, Any], message: str):
    full_name = profile.get("full_name")
    goto_page(
        session,
        action=lambda: session.page.goto(LINKEDIN_MESSAGING_URL),
        expected_url_pattern="/messaging",
        timeout=30_000,
        error_message="Error opening messaging",
    )
    try:
        # Search person
        session.page.locator('input[class^="msg-connections"]').type(full_name, delay=50)
        session.wait(0.5, 1)

        item = session.page.locator('div[class*="msg-connections-typeahead__search-result-row"]').first
        session.wait(0.5, 1)

        # Scroll into view + click (very reliable on LinkedIn)
        item.scroll_into_view_if_needed()
        item.click(delay=200)  # small delay between mousedown/mouseup = very human

        session.page.locator('div[class^="msg-form__contenteditable"]').type(message, delay=10)

        session.page.locator('button[class^="msg-form__send-button"]').click(delay=200)
        session.wait(0.5, 1)
        return True
    except Exception as e:
        public_identifier = profile.get("public_identifier")
        logger.error("Failed to send message to %s → %s", public_identifier, e)
        return False


if __name__ == "__main__":

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

    session = get_session(
        handle=handle,
    )
    session.ensure_browser()

    test_profile = {
        "full_name": "Bill Gates",
        "url": "https://www.linkedin.com/in/williamhgates/",
        "public_identifier": "williamhgates",
    }

    send_follow_up_message(
        handle=handle,
        profile=test_profile,
    )
