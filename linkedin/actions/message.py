# linkedin/actions/message.py
import json
import logging
import sys
from typing import Dict, Any

from linkedin.actions.connection_status import get_connection_status
from linkedin.navigation.enums import ProfileState
from linkedin.navigation.utils import goto_page
from linkedin.sessions.registry import get_session
from linkedin.templates.renderer import render_template

logger = logging.getLogger(__name__)

LINKEDIN_MESSAGING_URL = "https://www.linkedin.com/messaging/thread/new/"

SELECTORS = {
    "message_button": 'button[aria-label*="Message"]:visible',
    "overflow_action": 'button[id$="profile-overflow-action"]:visible',
    "message_option": 'div[aria-label$="to message"]:visible',
    "message_input": 'div[class*="msg-form__contenteditable"]:visible',
    "send_button": 'button[type="submit"][class*="msg-form"]:visible',
    "connections_input": 'input[class^="msg-connections"]',
    "search_result_row": 'div[class*="msg-connections-typeahead__search-result-row"]',
    "compose_input": 'div[class^="msg-form__contenteditable"]',
    "compose_send": 'button[class^="msg-form__send-button"]',
}


def send_follow_up_message(
        handle: str,
        profile: Dict[str, Any],
) -> str | None:
    """Send a follow-up message. Returns the message text if sent, None if skipped."""
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
        return None

    if template_file:
        message = render_template(session, template_file, template_type, profile)

    if _send_msg_pop_up(session, profile, message) or _send_message(session, profile, message):
        logger.debug("Message body: %s", message)
        return message

    return None


def _send_msg_pop_up(session: "AccountSession", profile: Dict[str, Any], message: str) -> bool:
    session.wait()
    page = session.page
    public_identifier = profile.get("public_identifier")

    try:
        direct = page.locator(SELECTORS["message_button"])
        if direct.count() > 0:
            direct.first.click()
            logger.debug("Opened Message popup (direct button)")
        else:
            more = page.locator(SELECTORS["overflow_action"]).first
            more.click()
            session.wait()
            msg_option = page.locator(SELECTORS["message_option"]).first
            msg_option.click()
            logger.debug("Opened Message via More → Message")

        session.wait()

        input_area = page.locator(SELECTORS["message_input"]).first

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

        send_btn = page.locator(SELECTORS["send_button"]).first
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
        session.page.locator(SELECTORS["connections_input"]).type(full_name, delay=50)
        session.wait(0.5, 1)

        item = session.page.locator(SELECTORS["search_result_row"]).first
        session.wait(0.5, 1)

        # Scroll into view + click (very reliable on LinkedIn)
        item.scroll_into_view_if_needed()
        item.click(delay=200)  # small delay between mousedown/mouseup = very human

        session.page.locator(SELECTORS["compose_input"]).type(message, delay=10)

        session.page.locator(SELECTORS["compose_send"]).click(delay=200)
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
