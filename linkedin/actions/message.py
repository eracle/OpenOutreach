# linkedin/actions/message.py
import json
import logging
from typing import Dict, Any

from playwright.sync_api import Error as PlaywrightError, Locator
from linkedin.browser.nav import goto_page, human_type, dump_page_html

logger = logging.getLogger(__name__)

LINKEDIN_MESSAGING_URL = "https://www.linkedin.com/messaging/thread/new/"
LINKEDIN_MESSAGING_THREAD_URL = "https://www.linkedin.com/messaging/thread/"

# Selector fallback chains: semantic/ARIA first, then class-based.
# LinkedIn A/B tests UI variants per account and renames classes often.
# Each key maps to a list tried in order; first with a match wins.
SELECTOR_CHAINS = {
    # ── Profile page ──
    "message_button": [
        'button[aria-label*="Message"]:visible',
        'button:has-text("Message"):visible',
    ],
    "overflow_action": [
        'button[id$="profile-overflow-action"]:visible',
        'button[aria-label="More actions"]:visible',
        'main section button:has-text("More"):visible',
    ],
    "message_option": [
        'div[role="menu"] a[href*="/messaging/"]:visible',
        'div[role="menuitem"]:has-text("Message"):visible',
        'div[aria-label$="to message"]:visible',
        'li:has-text("Message"):visible',
    ],
    # ── Popup / thread compose ──
    "message_input": [
        'div[role="textbox"][aria-label*="Write a message"]:visible',
        'div[role="textbox"][aria-label*="message"i]:visible',
        'div[class*="msg-form__contenteditable"]:visible',
        'div[contenteditable="true"]:visible',
    ],
    "send_button": [
        'button[type="submit"][class*="msg-form"]:visible',
        'form button[type="submit"]:visible',
        'button[type="submit"]:visible',
    ],
    # ── New thread: recipient search ──
    "connections_input": [
        'input[role="combobox"][placeholder*="name"]',
        'input[class*="msg-connections"]',
        'input[placeholder*="Type a name"]',
        'input[type="text"][aria-owns]',
    ],
    "search_result_row": [
        'ul[role="listbox"] li[role="option"]',
        'div[class*="msg-connections-typeahead__search-result-row"]',
        'li[class*="search-result"]',
    ],
    # ── Thread: compose area ──
    "compose_input": [
        'div[role="textbox"][aria-label*="Write a message"]',
        'div[role="textbox"][aria-label*="message"i]',
        'div[class*="msg-form__contenteditable"]',
        'div[contenteditable="true"]',
    ],
    "compose_send": [
        'button[type="submit"][class*="msg-form"]',
        'button[class*="send-btn"]',
        'button[class*="send-button"]',
        'form button[type="submit"]',
        'button[type="submit"]',
    ],
}


def _find(page, key: str, timeout: int = 5000) -> Locator:
    """Try each selector in the chain for *key*, return the first with matches.

    Raises PlaywrightError if none match within *timeout* ms.
    """
    chain = SELECTOR_CHAINS[key]
    for sel in chain:
        loc = page.locator(sel)
        try:
            loc.first.wait_for(state="attached", timeout=timeout)
            logger.debug("Selector hit for %s: %s", key, sel)
            return loc
        except (PlaywrightError, TimeoutError):
            continue
    tried = ", ".join(chain)
    raise PlaywrightError(f"No selector matched for '{key}'. Tried: {tried}")


def _open_compose_popup(session, page) -> bool:
    """Open the messaging compose popup on the current profile page.

    Tries the direct Message button first, then More → Message.
    Returns True if the popup was opened.
    """
    try:
        direct = _find(page, "message_button", timeout=3000)
        direct.first.click()
        logger.debug("Opened compose popup (direct button)")
        return True
    except PlaywrightError:
        pass

    try:
        _find(page, "overflow_action").first.click()
        session.wait()
        _find(page, "message_option").first.click()
        logger.debug("Opened compose popup (More → Message)")
        return True
    except PlaywrightError:
        return False


def _type_message(session, page, message: str):
    """Type a message into the compose popup input area."""
    input_area = _find(page, "message_input").first
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


def _click_send_and_verify(session, page) -> bool:
    """Click the send button and verify the message was actually sent.

    After clicking send, the input should clear. If text remains,
    the send failed silently.
    """
    send_btn = _find(page, "send_button").first
    send_btn.click(force=True)
    session.wait(4, 5)

    try:
        remaining = _find(page, "message_input", timeout=2000).first
        text = remaining.inner_text(timeout=2000).strip()
        if text:
            logger.error("Message input still has text after send → send failed")
            return False
    except (PlaywrightError, TimeoutError):
        pass  # input gone → popup closed → success

    return True


# ── Public entry point ────────────────────────────────────────────


def send_raw_message(session, profile: Dict[str, Any], message: str) -> bool:
    """Send an arbitrary message to a profile. Returns True if sent."""
    from linkedin.actions.search import _go_to_profile
    from linkedin.url_utils import public_id_to_url

    public_identifier = profile.get("public_identifier")
    _go_to_profile(session, public_id_to_url(public_identifier), public_identifier)

    if _send_msg_pop_up(session, profile, message):
        return True
    dump_page_html(session, profile, category="message_popup")

    if _send_message(session, profile, message):
        return True
    dump_page_html(session, profile, category="message_direct")

    if _send_message_via_api(session, profile, message):
        return True

    logger.error("All send methods failed for %s", public_identifier)
    return False


# ── Send strategies ───────────────────────────────────────────────


def _send_msg_pop_up(session, profile: Dict[str, Any], message: str) -> bool:
    """Open compose popup on the profile page, type, send, verify."""
    session.wait()
    page = session.page
    public_identifier = profile.get("public_identifier")

    try:
        if not _open_compose_popup(session, page):
            return False

        session.wait()
        _type_message(session, page, message)

        if not _click_send_and_verify(session, page):
            page.keyboard.press("Escape")
            session.wait()
            return False

        page.keyboard.press("Escape")
        session.wait()

        logger.info("Message sent to %s", public_identifier)
        return True

    except (PlaywrightError, TimeoutError) as e:
        logger.error("Failed to send message to %s → %s", public_identifier, e)
        return False


def _send_message(session, profile: Dict[str, Any], message: str) -> bool:
    """Navigate to /messaging/thread/new/, search by name, compose, send."""
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

        conn_input = _find(session.page, "connections_input").first
        conn_input.fill("")
        session.wait(0.5, 1)

        human_type(conn_input, full_name, min_delay=10, max_delay=50)
        session.wait(2, 3)

        # Verify the first search result matches the target name exactly
        item = _find(session.page, "search_result_row").first
        dt = item.locator("dt").first
        name_in_result = dt.inner_text(timeout=5_000).split("•")[0].strip()
        if name_in_result.lower() != full_name.lower():
            logger.error(
                "Recipient mismatch for %s: expected '%s' but got '%s' — aborting",
                public_identifier, full_name, name_in_result,
            )
            return False

        item.scroll_into_view_if_needed()
        item.click(delay=200)
        session.wait(1, 2)

        human_type(_find(session.page, "compose_input").first, message, min_delay=10, max_delay=50)

        _find(session.page, "compose_send").first.click(delay=200)
        session.wait(0.5, 1)
        logger.info("Message sent to %s (direct thread)", public_identifier)
        return True
    except (PlaywrightError, TimeoutError) as e:
        logger.error("Failed to send message to %s (direct thread) → %s", public_identifier, e)
        return False


def _send_message_via_api(
    session: "AccountSession",
    profile: Dict[str, Any],
    message: str,
    file_attachments: list[dict] | None = None,
) -> bool:
    """Last-resort fallback: send via Voyager Messaging API."""
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
        send_message(
            api,
            conversation_urn,
            message,
            mailbox_urn,
            file_attachments=file_attachments,
        )
        logger.info("Message sent to %s (API%s)", public_identifier, " + media" if file_attachments else "")
        return True
    except Exception as e:
        logger.error("API send failed for %s → %s", public_identifier, e)
        return False


def send_media_message(session, profile: Dict[str, Any], message: str, media_path: str) -> bool:
    """Send a message with a media attachment via UI file input.

    Navigates to the existing conversation, attaches the file, optionally
    types a message, and clicks Send.
    """
    import time
    from linkedin.db.chat import save_chat_message
    from linkedin.actions.conversations import find_conversation_urn, find_conversation_urn_via_navigation
    from linkedin.api.client import PlaywrightLinkedinAPI
    from crm.models import Lead

    public_identifier = profile.get("public_identifier")
    page = session.page

    # Find conversation thread URL
    lead = Lead.objects.filter(public_identifier=public_identifier).first()
    target_urn = lead.get_urn(session) if lead else None
    if not target_urn:
        logger.error("Cannot resolve URN for %s — media send failed", public_identifier)
        return False

    api = PlaywrightLinkedinAPI(session=session)
    mailbox_urn = session.self_profile["urn"]
    conv_urn = find_conversation_urn(api, target_urn, mailbox_urn)
    if not conv_urn:
        conv_urn = find_conversation_urn_via_navigation(session, target_urn)
    if not conv_urn:
        logger.error("No conversation found for %s — media send failed", public_identifier)
        return False

    # Extract thread ID from conversation URN and navigate
    # conv_urn format: urn:li:msg_conversation:(urn:li:fsd_profile:XXX,THREAD_ID)
    thread_id = conv_urn.rsplit(",", 1)[-1].rstrip(")")
    thread_url = f"{LINKEDIN_MESSAGING_THREAD_URL}{thread_id}/"

    try:
        goto_page(
            session,
            action=lambda: page.goto(thread_url),
            expected_url_pattern="/messaging",
            timeout=30_000,
            error_message="Error opening messaging thread",
        )
        session.wait()

        # Attach file via hidden file input
        file_input = page.locator('input[type="file"]')
        if file_input.count() == 0:
            logger.error("No file input found on messaging page for %s", public_identifier)
            return False

        file_input.first.set_input_files(media_path)
        logger.debug("Media attached via file input: %s", media_path)
        time.sleep(3)
        session.wait()

        # Type message if provided
        if message:
            msg_input = _find(page, "message_input")
            msg_input.first.fill(message)
            session.wait()

        # Click send
        send_btn = _find(page, "send_button")
        send_btn.first.click(force=True)
        session.wait(3, 4)

        save_chat_message(session, public_identifier, message or "[media]")
        logger.info("Media message sent to %s", public_identifier)
        return True

    except Exception as e:
        logger.error("Media send failed for %s → %s", public_identifier, e)
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

    conn_input = _find(session.page, "connections_input").first
    conn_input.fill("")
    session.wait(0.5, 1)
    human_type(conn_input, args.name, min_delay=10, max_delay=50)
    session.wait(3, 4)

    rows = _find(session.page, "search_result_row")
    count = rows.count()
    print(f"\n=== Found {count} result rows ===\n")
    for i in range(min(count, 3)):
        row = rows.nth(i)
        print(f"--- Row {i} inner_text ---")
        print(row.inner_text(timeout=5_000))
        print(f"\n--- Row {i} outer_html ---")
        print(row.evaluate("el => el.outerHTML"))
        print()
