# linkedin/actions/status.py
import logging
from typing import Dict, Any

from linkedin.actions.connect import SELECTORS as CONNECT_SELECTORS
from linkedin.actions.search import visit_profile
from linkedin.enums import ProfileState
from linkedin.browser.nav import find_top_card

logger = logging.getLogger(__name__)

SELECTORS = {
    "pending_button": '[aria-label*="Pending"]',
    "invite_to_connect": CONNECT_SELECTORS["invite_to_connect"],
    "more_button": CONNECT_SELECTORS["more_button"],
    "connect_option": CONNECT_SELECTORS["connect_option"],
}


def get_connection_status(
        session: "AccountSession",
        profile: Dict[str, Any],
) -> ProfileState:
    """
    Detects connection status.

    Priority:
      1. API connection_degree — degree 1 = CONNECTED, degree 2/3 = QUALIFIED.
         Callers should pass a fresh profile (via Lead.refresh_profile) so the
         degree is up-to-date.
      2. UI inspection fallback — only when API returns None.
    """
    from crm.models import Lead

    public_identifier = profile.get("public_identifier")
    session.ensure_browser()

    logger.debug("Checking connection status → %s", public_identifier)

    # Fresh API fetch — connection_degree in the passed profile dict may be stale
    lead = Lead.objects.get(public_identifier=public_identifier)
    lead.refresh_profile(session, profile_dict=profile)
    degree = profile.get("connection_degree")

    if degree == 1:
        logger.debug("API reports 1st degree → CONNECTED")
        return ProfileState.CONNECTED
    if degree in (2, 3):
        logger.debug("API reports degree %d → NOT_CONNECTED", degree)
        return ProfileState.QUALIFIED

    # --- Fallback: UI inspection (API returned None) ---
    logger.debug("API degree=None → falling back to UI inspection")
    visit_profile(session, profile)
    session.wait()

    top_card = find_top_card(session)

    has_pending = top_card.locator(SELECTORS["pending_button"]).count() > 0
    has_connect = top_card.locator(SELECTORS["invite_to_connect"]).count() > 0

    if has_pending:
        logger.debug("Detected 'Pending' button → PENDING")
        return ProfileState.PENDING

    if has_connect:
        logger.debug("Found 'Connect' button → NOT_CONNECTED")
        return ProfileState.QUALIFIED

    has_connect_in_more = _has_connect_in_more(session, top_card)
    if has_connect_in_more:
        logger.debug("Found 'Connect' in More menu → NOT_CONNECTED")
        return ProfileState.QUALIFIED

    logger.debug("No clear indicators → defaulting to NOT_CONNECTED")
    return ProfileState.QUALIFIED


def _has_connect_in_more(session, top_card) -> bool:
    more = top_card.locator(SELECTORS["more_button"])
    if more.count() == 0:
        return False
    more.first.click()
    session.wait()
    # Dropdown may render as a portal outside top_card, so search page-wide
    found = session.page.locator(SELECTORS["connect_option"]).count() > 0
    if not found:
        session.page.keyboard.press("Escape")
    return found


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
