# linkedin/actions/status.py
import logging
from typing import Dict, Any, Optional

from linkedin.actions.connect import SELECTORS as CONNECT_SELECTORS
from linkedin.actions.search import visit_profile
from linkedin.enums import ProfileState
from linkedin.browser.nav import find_top_card, dump_page_html

logger = logging.getLogger(__name__)

SELECTORS = {
    "pending_button": '[aria-label*="Pending"]',
    "invite_to_connect": CONNECT_SELECTORS["invite_to_connect"],
    "more_button": CONNECT_SELECTORS["more_button"],
    "connect_option": CONNECT_SELECTORS["connect_option"],
}


# ── API layer ──────────────────────────────────────────────────────

def _fetch_degree(session, public_identifier: str, profile: Dict[str, Any]) -> Optional[int]:
    """Return connection degree from API, trying two decorations.

    1. Full profile scrape (FullProfileWithEntities) — mutates ``profile``
       in place with the fresh fields and reads ``connection_degree``
       from the response.
    2. If that returns None, fall back to the lightweight
       TopCardSupplementary endpoint.
    """
    from linkedin.api.client import PlaywrightLinkedinAPI

    api = PlaywrightLinkedinAPI(session=session)
    fresh, _raw = api.get_profile(public_identifier=public_identifier)
    if fresh:
        profile.update(fresh)
    degree = profile.get("connection_degree")

    if degree is None:
        degree = api.get_connection_degree(public_identifier)
        logger.debug("TopCard degree lookup → %s", degree)

    return degree


# ── UI layer ───────────────────────────────────────────────────────

def _inspect_ui(session, profile: Dict[str, Any]) -> ProfileState:
    """Determine connection status from profile page buttons.

    Returns PENDING, QUALIFIED (connect available), or CONNECTED
    (no connect/pending buttons found).
    """
    visit_profile(session, profile)
    session.wait()
    top_card = find_top_card(session)

    if top_card.locator(SELECTORS["pending_button"]).count() > 0:
        logger.debug("UI → 'Pending' button detected")
        return ProfileState.PENDING

    if top_card.locator(SELECTORS["invite_to_connect"]).count() > 0:
        logger.debug("UI → 'Connect' button detected")
        return ProfileState.QUALIFIED

    if _has_connect_in_more(session, top_card):
        logger.debug("UI → 'Connect' in More menu")
        return ProfileState.QUALIFIED

    logger.debug("UI → no connect/pending indicators — dumping page")
    dump_page_html(session, profile, category="status")
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


# ── Public entry point ─────────────────────────────────────────────

def get_connection_status(
        session: "AccountSession",
        profile: Dict[str, Any],
) -> ProfileState:
    """Detect connection status via API with UI fallback.

    Priority:
      1. API degree (two decorations) — degree 1 = CONNECTED.
      2. For degree 2/3 or None — UI inspection decides between
         PENDING, QUALIFIED, and CONNECTED.
    """
    public_identifier = profile.get("public_identifier")
    session.ensure_browser()
    logger.debug("Checking connection status → %s", public_identifier)

    degree = _fetch_degree(session, public_identifier, profile)

    if degree == 1:
        logger.debug("API degree 1 → CONNECTED")
        return ProfileState.CONNECTED

    # degree 2/3 or None — let the UI decide
    return _inspect_ui(session, profile)


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

    logger.info("Checking connection status as %s → %s", session, args.profile)
    status = get_connection_status(session, test_profile)
    logger.info("Connection status → %s", status.value)
