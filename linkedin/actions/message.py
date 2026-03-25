# linkedin/actions/message.py
import json
import logging
from typing import Dict, Any

from playwright.sync_api import Error as PlaywrightError
from linkedin.browser.nav import goto_page, human_type

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


def send_raw_message(session, profile: Dict[str, Any], message: str) -> bool:
    """Send an arbitrary message to a profile. Returns True if sent."""
    public_identifier = profile.get("public_identifier")

    sent = (
        _send_message_via_api(session, profile, message)
        or _send_msg_pop_up(session, profile, message)
        or _send_message(session, profile, message)
    )
    if not sent:
        logger.error("All send methods failed for %s", public_identifier)
        return False

    logger.info("Message sent to %s: %s", public_identifier, message)
    return True



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

    except (PlaywrightError, TimeoutError) as e:
        logger.error("Failed to send message to %s → %s", public_identifier, e)
        return False


def _send_message(session: "AccountSession", profile: Dict[str, Any], message: str) -> bool:
    public_identifier = profile.get("public_identifier")
    full_name = profile.get("full_name")
    if not full_name:
        logger.error("Cannot send via direct thread: no full_name for %s", public_identifier)
        return False
    try:
        goto_page(
            session,
            action=lambda: session.page.goto(LINKEDIN_MESSAGING_URL),
            expected_url_pattern="/messaging",
            timeout=30_000,
            error_message="Error opening messaging",
        )

        conn_input = session.page.locator(SELECTORS["connections_input"])
        # Clear any pre-existing text in the search field
        conn_input.fill("")
        session.wait(0.5, 1)

        # Type the name and wait for search results to update
        human_type(conn_input, full_name, min_delay=10, max_delay=50)
        session.wait(2, 3)

        # Verify the first search result matches the target name exactly
        item = session.page.locator(SELECTORS["search_result_row"]).first
        dt = item.locator("dt").first
        name_in_result = dt.inner_text(timeout=5_000).split("•")[0].strip()
        if name_in_result.lower() != full_name.lower():
            logger.error(
                "Recipient mismatch for %s: expected '%s' but got '%s' — aborting",
                public_identifier, full_name, name_in_result,
            )
            return False

        # Scroll into view + click (very reliable on LinkedIn)
        item.scroll_into_view_if_needed()
        item.click(delay=200)  # small delay between mousedown/mouseup = very human

        human_type(session.page.locator(SELECTORS["compose_input"]), message, min_delay=10, max_delay=50)

        session.page.locator(SELECTORS["compose_send"]).click(delay=200)
        session.wait(0.5, 1)
        logger.info("Message sent to %s (direct thread)", public_identifier)
        return True
    except (PlaywrightError, TimeoutError) as e:
        logger.error("Failed to send message to %s (direct thread) → %s", public_identifier, e)
        return False


def _send_message_via_api(session: "AccountSession", profile: Dict[str, Any], message: str) -> bool:
    """Last-resort fallback: send via Voyager Messaging API.

    Requires profile dict to contain 'urn' (target profile URN).
    """
    from linkedin.api.client import PlaywrightLinkedinAPI
    from linkedin.api.messaging import send_message
    from linkedin.actions.conversations import find_conversation_urn, find_conversation_urn_via_navigation

    public_identifier = profile.get("public_identifier")
    target_urn = profile.get("urn")
    if not target_urn:
        logger.error("API send failed for %s → no URN in profile dict", public_identifier)
        return False

    mailbox_urn = session.self_profile["urn"]
    api = PlaywrightLinkedinAPI(session=session)

    conversation_urn = find_conversation_urn(api, target_urn, mailbox_urn)
    if not conversation_urn:
        conversation_urn = find_conversation_urn_via_navigation(session, target_urn)
    if not conversation_urn:
        logger.error("API send failed for %s → no conversation found", public_identifier)
        return False

    try:
        send_message(api, conversation_urn, message, mailbox_urn)
        logger.info("Message sent to %s (API fallback)", public_identifier)
        return True
    except Exception as e:
        logger.error("API send failed for %s → %s", public_identifier, e)
        return False


if __name__ == "__main__":
    from linkedin.browser.registry import cli_parser, cli_session

    parser = cli_parser("Debug LinkedIn messaging search results")
    parser.add_argument("--name", required=True, help="Full name to search for")
    args = parser.parse_args()
    session = cli_session(args)
    session.ensure_browser()

    print(f"Searching for '{args.name}' ...")

    goto_page(
        session,
        action=lambda: session.page.goto(LINKEDIN_MESSAGING_URL),
        expected_url_pattern="/messaging",
        timeout=30_000,
        error_message="Error opening messaging",
    )

    conn_input = session.page.locator(SELECTORS["connections_input"])
    conn_input.fill("")
    session.wait(0.5, 1)
    human_type(conn_input, args.name, min_delay=10, max_delay=50)
    session.wait(3, 4)

    rows = session.page.locator(SELECTORS["search_result_row"])
    count = rows.count()
    print(f"\n=== Found {count} result rows ===\n")
    for i in range(min(count, 3)):
        row = rows.nth(i)
        print(f"--- Row {i} inner_text ---")
        print(row.inner_text(timeout=5_000))
        print(f"\n--- Row {i} outer_html ---")
        print(row.evaluate("el => el.outerHTML"))
        print()

