# linkedin/actions/connect.py
import logging
from typing import Dict, Any

from playwright.sync_api import Error as PlaywrightError
from linkedin.enums import ProfileState
from linkedin.exceptions import SkipProfile, ReachedConnectionLimit
from linkedin.browser.nav import find_top_card, dump_page_html, human_type

logger = logging.getLogger(__name__)

SELECTORS = {
    "weekly_limit": 'div[class*="ip-fuse-limit-alert__warning"]',
    "invite_to_connect": (
        '[aria-label*="Invite"][aria-label*="to connect"]:visible, '
        'a:has(span:text-is("Connect")):visible, '
        'button:has(span:text-is("Connect")):visible'
    ),
    "error_toast": 'div[data-test-artdeco-toast-item-type="error"]',
    "more_button": (
        'button[aria-label="More"]:visible, '
        'button[id*="overflow"]:visible, '
        'button[aria-label*="More actions"]:visible, '
        'button:has(span:text-is("More")):visible'
    ),
    "connect_option": (
        'div[role="button"][aria-label^="Invite"][aria-label*=" to connect"], '
        'div[role="button"]:text-is("Connect"), '
        '[role="menuitem"][aria-label*="Connect"], '
        '[role="menuitem"]:has-text("Connect"), '
        'li:text-is("Connect"), '
        'span[role="button"]:text-is("Connect")'
    ),
    # Note flow selectors — LinkedIn A/B tests these, so each is a fallback chain
    "add_note_button": (
        'button[aria-label*="Add a note"]:visible, '
        'button:has-text("Add a note"):visible'
    ),
    "note_textarea": (
        'textarea[name="message"]:visible, '
        'textarea[placeholder*="note"i]:visible, '
        'textarea[maxlength="300"]:visible, '
        'textarea:visible'
    ),
    "send_with_note": (
        'button[aria-label*="Send invitation"]:visible, '
        'button[aria-label*="Send now"]:visible, '
        'button:has-text("Send"):visible'
    ),
    "send_now": (
        'button:has-text("Send now"), '
        'button[aria-label*="Send without"], '
        'button[aria-label*="Send invitation"]'
    ),
}

NOTE_MAX_CHARS = 295  # LinkedIn hard limit is 300; keep a small buffer


def generate_connection_note(session, profile: Dict[str, Any]) -> str | None:
    """Generate a personalized connection note via LLM. Returns None on any failure."""
    try:
        from langchain_openai import ChatOpenAI
        from linkedin.conf import get_llm_config

        llm_api_key, ai_model, llm_api_base = get_llm_config()
        if not llm_api_key:
            return None

        first_name = profile.get("first_name", "")
        last_name = profile.get("last_name", "")
        headline = profile.get("headline", "")
        summary = (profile.get("summary") or "")[:300]

        # Need at least a headline or name to write a specific note
        if not headline and not first_name:
            return None

        campaign = session.campaign
        product_docs = (getattr(campaign, "product_docs", "") or "")[:400]
        campaign_objective = (getattr(campaign, "campaign_objective", "") or "")[:300]

        profile_info = f"Name: {first_name} {last_name}".strip()
        if headline:
            profile_info += f"\nHeadline: {headline}"
        if summary:
            profile_info += f"\nSummary: {summary}"

        system_prompt = (
            "You write LinkedIn connection request notes. Write one short, genuine note.\n\n"
            "Rules:\n"
            "- Under 280 characters. This is a hard limit.\n"
            "- Reference something specific from their profile: their role, company, or what they are working on.\n"
            "- Sound like a real person who noticed their work, not a bot.\n"
            "- No sales pitch. No mention of job seeking. No generic openers like 'I would love to connect'.\n"
            "- No em dashes, no exclamation points, no emoji.\n"
            "- First person, conversational, specific.\n"
            "- Do NOT start with Hi, Hello, or their name.\n"
            "- Return ONLY the note text. No quotes, no explanation.\n\n"
            f"About me (context only, do not repeat this):\n{product_docs}\n\n"
            f"Outreach objective:\n{campaign_objective}"
        )

        user_prompt = f"Write a connection note for:\n\n{profile_info}"

        llm = ChatOpenAI(
            model=ai_model,
            temperature=0.9,
            api_key=llm_api_key,
            base_url=llm_api_base,
            timeout=30,
        )
        response = llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])

        note = response.content.strip().strip('"\'')
        if len(note) > NOTE_MAX_CHARS:
            note = note[:NOTE_MAX_CHARS - 3] + "..."

        if not note:
            return None

        logger.info("Generated connection note (%d chars): %s", len(note), note)
        return note

    except Exception as exc:
        logger.warning("Note generation failed, falling back to no-note: %s", exc)
        return None


def send_connection_request(
        session: "AccountSession",
        profile: Dict[str, Any],
) -> ProfileState:
    """Send a LinkedIn connection request, with a personalized note when possible."""
    public_identifier = profile.get("public_identifier")

    if not _connect_direct(session) and not _connect_via_more(session):
        logger.debug("Connect button not found for %s", public_identifier)
        dump_page_html(session, profile)
        return ProfileState.QUALIFIED

    # Try to send with a personalized note; fall back to no-note silently
    note = generate_connection_note(session, profile)
    if note and _click_with_note(session, note):
        logger.debug("Connection request with note submitted for %s", public_identifier)
    else:
        _click_without_note(session)
        logger.debug("Connection request without note submitted for %s", public_identifier)

    _check_weekly_invitation_limit(session)
    return ProfileState.PENDING


def _check_weekly_invitation_limit(session):
    weekly_invitation_limit = session.page.locator(SELECTORS["weekly_limit"])
    if weekly_invitation_limit.count() > 0:
        raise ReachedConnectionLimit("Weekly connection limit pop up appeared")


def _connect_direct(session):
    session.wait()
    top_card = find_top_card(session)
    direct = top_card.locator(SELECTORS["invite_to_connect"])
    if direct.count() == 0:
        return False

    direct.first.click()
    logger.debug("Clicked direct 'Connect' button")

    error = session.page.locator(SELECTORS["error_toast"])
    if error.count() > 0:
        raise SkipProfile(f"{error.inner_text().strip()}")

    return True


def _connect_via_more(session):
    session.wait()
    top_card = find_top_card(session)
    page = session.page

    connect_option = page.locator(SELECTORS["connect_option"])

    if connect_option.count() == 0:
        more = top_card.locator(SELECTORS["more_button"])
        if more.count() == 0:
            return False
        more.first.click()
        session.wait()

    connect_option = page.locator(SELECTORS["connect_option"])
    if connect_option.count() == 0:
        return False
    connect_option.first.click()
    logger.debug("Used 'More -> Connect' flow")
    return True


def _click_with_note(session, note: str) -> bool:
    """Open 'Add a note', type the note with human delays, then send.

    Returns True if the full flow completed, False if any step failed
    (caller falls back to no-note in that case).
    """
    page = session.page
    session.wait()

    add_note_btn = page.locator(SELECTORS["add_note_button"])
    try:
        add_note_btn.first.wait_for(state="visible", timeout=5000)
    except (PlaywrightError, Exception):
        logger.debug("'Add a note' button not visible — falling back to no-note")
        return False

    add_note_btn.first.click()
    session.wait()

    textarea = page.locator(SELECTORS["note_textarea"])
    try:
        textarea.first.wait_for(state="visible", timeout=5000)
    except (PlaywrightError, Exception):
        logger.debug("Note textarea not visible — falling back to no-note")
        return False

    # Human-like typing with randomized per-keystroke delay
    human_type(textarea.first, note)
    session.wait()

    send_btn = page.locator(SELECTORS["send_with_note"])
    try:
        send_btn.first.wait_for(state="visible", timeout=5000)
    except (PlaywrightError, Exception):
        logger.debug("Send button not visible after note — falling back to no-note")
        return False

    send_btn.first.click(force=True)
    session.wait()
    return True


def _click_without_note(session):
    """Click 'Send now' / 'Send without a note' — no note flow."""
    session.wait()
    send_btn = session.page.locator(SELECTORS["send_now"])
    send_btn.first.click(force=True)
    session.wait()
    logger.debug("Connection request submitted (no note)")


if __name__ == "__main__":
    from linkedin.browser.registry import cli_parser, cli_session
    from linkedin.actions.status import get_connection_status

    parser = cli_parser("Send a LinkedIn connection request")
    parser.add_argument("--profile", required=True, help="Public identifier of the target profile")
    args = parser.parse_args()
    session = cli_session(args)

    test_profile = {
        "url": f"https://www.linkedin.com/in/{args.profile}/",
        "public_identifier": args.profile,
    }

    logger.info("Testing connection request as %s -> %s", session, args.profile)
    connection_status = get_connection_status(session, test_profile)
    logger.info("Pre-check status -> %s", connection_status.value)

    if connection_status in (ProfileState.CONNECTED, ProfileState.PENDING):
        logger.info("Skipping – already %s", connection_status.value)
    else:
        status = send_connection_request(session=session, profile=test_profile)
        logger.info("Finished -> Status: %s", status.value)
