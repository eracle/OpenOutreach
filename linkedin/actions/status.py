# linkedin/actions/status.py
import logging
from typing import Dict, Any

from linkedin.actions.search import visit_profile
from linkedin.enums import ProfileState
from linkedin.browser.nav import find_top_card

logger = logging.getLogger(__name__)

SELECTORS = {
    "pending_button": '[aria-label*="Pending"]',
    "invite_to_connect": (
        'button[aria-label*="Invite"][aria-label*="to connect"]:visible, '
        'a:has(span:text-is("Connect")):visible'
    ),
    "message_button": 'a[href*="/messaging/compose/"]:visible, button:has-text("Message"):visible',
}


def get_connection_status(
        session: "AccountSession",
        profile: Dict[str, Any],
) -> ProfileState:
    """
    Reliably detects connection status using UI inspection.
    Only trusts degree=1 as CONNECTED. Everything else is verified on the page.
    """
    # Ensure browser is ready (safe to call multiple times)
    session.ensure_browser()
    visit_profile(session, profile)
    session.wait()

    logger.debug("Checking connection status → %s", profile.get("public_identifier"))

    degree = profile.get("connection_degree", None)

    # Fast path: API says 1st degree → trust it
    if degree == 1:
        logger.debug("API reports 1st degree → instantly trusted as CONNECTED")
        return ProfileState.CONNECTED

    logger.debug("connection_degree=%s → falling back to UI inspection", degree or "None")

    top_card = find_top_card(session)

    # Check pending button in DOM first (most reliable)
    if top_card.locator(SELECTORS["pending_button"]).count() > 0:
        logger.debug("Detected 'Pending' button → PENDING")
        return ProfileState.PENDING

    main_text = top_card.inner_text()

    # Text-based indicators, checked in priority order
    TEXT_INDICATORS = [
        (["Pending"], ProfileState.PENDING, "Detected 'Pending' text → PENDING"),
        (["1st", "1st degree", "1º", "1er"], ProfileState.CONNECTED, "Confirmed 1st degree via text → CONNECTED"),
    ]
    for keywords, state, msg in TEXT_INDICATORS:
        if any(kw in main_text for kw in keywords):
            logger.debug(msg)
            return state

    has_connect = top_card.locator(SELECTORS["invite_to_connect"]).count() > 0
    has_message = top_card.locator(SELECTORS["message_button"]).count() > 0

    # Connect button wins over Message (InMail/Open Profile show Message without being connected)
    if has_connect:
        logger.debug("Found 'Connect' button → NOT_CONNECTED")
        return ProfileState.QUALIFIED

    if has_message and not has_connect:
        logger.debug("Detected 'Message' button (no Connect) → CONNECTED")
        return ProfileState.CONNECTED

    if degree:
        logger.debug("No UI indicators but degree=%s → NOT_CONNECTED", degree)
        return ProfileState.QUALIFIED

    logger.debug("No clear indicators → defaulting to NOT_CONNECTED")
    return ProfileState.QUALIFIED


if __name__ == "__main__":
    from linkedin.browser.registry import cli_parser, cli_session

    parser = cli_parser("Check LinkedIn connection status")
    parser.add_argument("--profile", required=True, help="Public identifier of the target profile")
    args = parser.parse_args()
    session = cli_session(args)

    test_profile = {
        "url": f"https://www.linkedin.com/in/{args.profile}/",
        "public_identifier": args.profile,
    }

    print(f"Checking connection status as {session} → {args.profile}")
    status = get_connection_status(session, test_profile)
    print(f"Connection status → {status.value}")
