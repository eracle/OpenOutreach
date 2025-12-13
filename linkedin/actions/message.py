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

LINKEDIN_MESSAGING_URL = "https://www.linkedin.com/messaging/thread/new/"


def send_follow_up_message(
        key: SessionKey,
        profile: Dict[str, Any],
        *,
        template_file: Optional[str] = None,
        template_type: str = "jinja",
        message: Optional[str] = None,
):
    session = AccountSessionRegistry.get_or_create(
        handle=key.handle,
        campaign_name=key.campaign_name,
        csv_hash=key.csv_hash,
    )
    session.ensure_browser()

    search_profile(session, profile)

    if message is None and template_file:
        message = render_template(session, template_file, template_type, profile)
    elif message is None:
        message = ""

    return send_message_to_profile(session, profile, message)


def get_messaging_availability(session: "AccountSession", profile: Dict[str, Any]) -> bool:
    status = get_connection_status(session, profile)
    public_identifier = profile.get("public_identifier")
    logger.debug("Messaging check → %s → %s", public_identifier, status.value)

    if status == ConnectionStatus.CONNECTED:
        return True

    if status == ConnectionStatus.NOT_CONNECTED:
        logger.info("Messaging blocked → not connected with %s", public_identifier)
    elif status == ConnectionStatus.PENDING:
        logger.info("Messaging blocked → still pending with %s", public_identifier)

    return False


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
            page.evaluate(f"""() => navigator.clipboard.writeText(`{message.replace("`", "\\`")}`)""")
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


def send_message_to_profile(
        session: "AccountSession",
        profile: Dict[str, Any],
        message: str,
) -> MessageStatus:
    if not get_messaging_availability(session, profile):
        logger.info("Message skipped → not connected")
        return MessageStatus.SKIPPED
    s1 = _send_msg_pop_up(session, profile, message)
    s2 = s1 or _send_message(session, profile, message)
    success = s2
    logger.info(f"Message sent: {message}") if success else None
    return MessageStatus.SENT if success else MessageStatus.SKIPPED


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

    session, _ = AccountSessionRegistry.get_or_create_from_path(
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
