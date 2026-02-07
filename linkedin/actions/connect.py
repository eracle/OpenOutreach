# linkedin/actions/connect.py
import logging
from typing import Dict, Any

from linkedin.navigation.enums import ProfileState
from linkedin.navigation.exceptions import SkipProfile, ReachedConnectionLimit
from linkedin.navigation.utils import get_top_card
from linkedin.sessions.registry import get_session

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
        handle: str,
        profile: Dict[str, Any],
) -> ProfileState:
    """
    Sends a LinkedIn connection request WITHOUT a note (fastest & safest).
    All note-sending logic preserved below for future use.
    """
    from linkedin.actions.connection_status import get_connection_status

    session = get_session(
        handle=handle,
    )

    public_identifier = profile.get('public_identifier')

    logger.debug("Checking current connection status...")
    connection_status = get_connection_status(session, profile)
    logger.info("Current status → %s", connection_status.value)

    skip_reasons = {
        ProfileState.CONNECTED: "Already connected",
        ProfileState.PENDING: "Invitation already pending",
    }

    if connection_status in skip_reasons:
        logger.info("Skipping %s – %s", public_identifier, skip_reasons[connection_status])
        return connection_status

    # Send invitation WITHOUT note (current active flow)
    s1 = _connect_direct(session)
    s2 = s1 or _connect_via_more(session)

    s3 = s2 and _click_without_note(session)
    s4 = s3 and _check_weekly_invitation_limit(session)
    success = s4

    status = ProfileState.PENDING if success else ProfileState.ENRICHED
    logger.info(f"Connection request {status} → {public_identifier}")
    return status


def _check_weekly_invitation_limit(session):
    weekly_invitation_limit = session.page.locator(SELECTORS["weekly_limit"])
    if weekly_invitation_limit.count() > 0:
        raise ReachedConnectionLimit("Weekly connection limit pop up appeared")

    return True


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

    return True


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

    print(f"Testing connection request as @{handle} )")
    status = send_connection_request(
        handle=handle,
        profile=test_profile,
    )

    print(f"Finished → Status: {status.value}")
