# linkedin/actions/connect.py
import logging
from typing import Dict, Any

from linkedin.navigation.enums import ProfileState
from linkedin.navigation.exceptions import SkipProfile, ReachedConnectionLimit
from linkedin.navigation.utils import get_top_card

logger = logging.getLogger(__name__)

SELECTORS = {
    "weekly_limit": 'div[class*="ip-fuse-limit-alert__warning"]',
    "invite_to_connect": 'button[aria-label*="Invite"][aria-label*="to connect"]:visible',
    "error_toast": 'div[data-test-artdeco-toast-item-type="error"]',
    "more_button": 'button[id*="overflow"]:visible, button[aria-label*="More actions"]:visible',
    "connect_option": 'div[role="button"][aria-label^="Invite"][aria-label*=" to connect"]',
    "send_now": 'button:has-text("Send now"), button[aria-label*="Send without"], button[aria-label*="Send invitation"]:not([aria-label*="note"])',
    "add_note": 'button:has-text("Add a note")',
    "note_textarea": 'textarea#custom-message, textarea[name="message"]',
    "send_invitation": 'button:has-text("Send"), button[aria-label*="Send invitation"]',
}


def send_connection_request(
        session: "AccountSession",
        profile: Dict[str, Any],
) -> ProfileState:
    """
    Sends a LinkedIn connection request WITHOUT a note (fastest & safest).

    Assumes the profile page is already loaded (caller navigates via
    ``get_connection_status`` or ``search_profile`` beforehand).
    """
    public_identifier = profile.get('public_identifier')

    # Send invitation WITHOUT note (current active flow)
    if not _connect_direct(session) and not _connect_via_more(session):
        logger.debug("Connect button not found for %s — staying ENRICHED", public_identifier)
        return ProfileState.ENRICHED

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
    top_card = get_top_card(session)
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
    top_card = get_top_card(session)

    # Fallback: More → Connect
    more = top_card.locator(SELECTORS["more_button"])
    if more.count() == 0:
        return False
    more.first.click()

    session.wait()

    connect_option = top_card.locator(SELECTORS["connect_option"])
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


# ===================================================================
# FUTURE: Send with personalized note (just uncomment when ready)
# ===================================================================
def _perform_send_invitation_with_note(session, message: str):
    """Full flow with custom note – ready to enable anytime."""
    session.wait()
    top_card = get_top_card(session)

    direct = top_card.locator(SELECTORS["invite_to_connect"])
    if direct.count() > 0:
        direct.first.click()
    else:
        more = top_card.locator(SELECTORS["more_button"]).first
        more.click()
        session.wait()
        session.page.locator(SELECTORS["connect_option"]).first.click()

    session.wait()
    session.page.locator(SELECTORS["add_note"]).first.click()
    session.wait()

    textarea = session.page.locator(SELECTORS["note_textarea"])
    textarea.first.fill(message)
    session.wait()
    logger.debug("Filled note (%d chars)", len(message))

    session.page.locator(SELECTORS["send_invitation"]).first.click(force=True)
    session.wait()
    logger.debug("Connection request with note sent")


if __name__ == "__main__":
    import sys
    from linkedin.actions.connection_status import get_connection_status
    from linkedin.sessions.registry import get_session

    if len(sys.argv) != 2:
        print("Usage: python -m linkedin.actions.connect <handle>")
        sys.exit(1)

    handle = sys.argv[1]

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

    session = get_session(handle=handle)
    print(f"Testing connection request as @{handle} )")

    connection_status = get_connection_status(session, test_profile)
    print(f"Pre-check status → {connection_status.value}")

    if connection_status in (ProfileState.CONNECTED, ProfileState.PENDING):
        print(f"Skipping – already {connection_status.value}")
    else:
        status = send_connection_request(session=session, profile=test_profile)
        print(f"Finished → Status: {status.value}")
