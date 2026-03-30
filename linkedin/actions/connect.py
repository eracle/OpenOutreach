# linkedin/actions/connect.py
import logging
from typing import Dict, Any

from linkedin.enums import ProfileState
from linkedin.exceptions import SkipProfile, ReachedConnectionLimit
from linkedin.browser.nav import find_top_card, dump_page_html

logger = logging.getLogger(__name__)

SELECTORS = {
    "weekly_limit": 'div[class*="ip-fuse-limit-alert__warning"]',
    "invite_to_connect": (
        'button[aria-label*="Invite"][aria-label*="to connect"]:visible, '
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
        'li:text-is("Connect"), '
        'span[role="button"]:text-is("Connect")'
    ),
    "send_now": 'button:has-text("Send now"), button[aria-label*="Send without"], button[aria-label*="Send invitation"]',
}


def send_connection_request(
        session: "AccountSession",
        profile: Dict[str, Any],
) -> ProfileState:
    """
    Sends a LinkedIn connection request WITHOUT a note (fastest & safest).

    Assumes the profile page is already loaded (caller navigates via
    ``get_connection_status`` or ``visit_profile`` beforehand).
    """
    public_identifier = profile.get('public_identifier')

    # Send invitation WITHOUT note (current active flow)
    if not _connect_direct(session) and not _connect_via_more(session):
        logger.debug("Connect button not found for %s — staying at current stage", public_identifier)
        dump_page_html(session, profile)
        return ProfileState.QUALIFIED

    _click_without_note(session)
    _check_weekly_invitation_limit(session)

    logger.debug("Connection request submitted for %s", public_identifier)
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

    # Dropdown may render as a portal outside top_card, so search page-wide
    connect_option = page.locator(SELECTORS["connect_option"])

    # Connect option may already be visible (More dropdown opened by status check)
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
    logger.debug("Used 'More → Connect' flow")

    return True


def _click_without_note(session):
    """Click flow: sends connection request instantly without note."""
    session.wait()

    # Click "Send now" / "Send without a note"
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

    print(f"Testing connection request as {session} → {args.profile}")

    connection_status = get_connection_status(session, test_profile)
    print(f"Pre-check status → {connection_status.value}")

    if connection_status in (ProfileState.CONNECTED, ProfileState.PENDING):
        print(f"Skipping – already {connection_status.value}")
    else:
        status = send_connection_request(session=session, profile=test_profile)
        print(f"Finished → Status: {status.value}")
