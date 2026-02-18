# linkedin/actions/search.py

import logging
from typing import Dict, Any
from urllib.parse import urlparse, parse_qs, urlencode

from linkedin.navigation.utils import goto_page, human_type

logger = logging.getLogger(__name__)

SELECTORS = {
    "search_bar": "//input[contains(@placeholder, 'Search')]",
    "profile_links": 'a[href*="/in/"]',
}


def _go_to_profile(session: "AccountSession", url: str, public_identifier: str):
    if f"/in/{public_identifier}" in session.page.url:
        return
    logger.debug("Direct navigation → %s", public_identifier)
    goto_page(
        session,
        action=lambda: session.page.goto(url),
        expected_url_pattern=f"/in/{public_identifier}",
        error_message="Failed to navigate to the target profile"
    )


def search_profile(session: "AccountSession", profile: Dict[str, Any]):
    public_identifier = profile.get("public_identifier")

    # Ensure browser is alive before doing anything
    session.ensure_browser()

    if f"/in/{public_identifier}" in session.page.url:
        return

    #_simulate_human_search(session, profile)

    url = profile.get("url")
    _go_to_profile(session, url, public_identifier)


def _initiate_search(session: "AccountSession", keyword: str):
    """Navigate directly to LinkedIn People search results for *keyword*."""
    page = session.page
    params = urlencode({"keywords": keyword, "origin": "GLOBAL_SEARCH_HEADER"})
    url = f"https://www.linkedin.com/search/results/people/?{params}"

    goto_page(
        session,
        action=lambda: page.goto(url),
        expected_url_pattern="/search/results/people/",
        error_message="Failed to reach People search results",
    )


def _paginate_to_next_page(session: "AccountSession", page_num: int):
    page = session.page
    current = urlparse(page.url)
    params = parse_qs(current.query)
    params["page"] = [str(page_num)]
    new_url = current._replace(query=urlencode(params, doseq=True)).geturl()

    logger.debug("Scanning search page %s", page_num)
    goto_page(
        session,
        action=lambda: page.goto(new_url),
        expected_url_pattern="/search/results/",
        error_message="Pagination failed"
    )


def search_people(session: "AccountSession", keyword: str, page: int = 1):
    """Search LinkedIn People by keyword and navigate to the given page.

    Profile discovery happens automatically — goto_page() calls
    _extract_in_urls() → _enrich_new_urls().
    """
    session.ensure_browser()
    _initiate_search(session, keyword)
    if page > 1:
        _paginate_to_next_page(session, page)


def _simulate_human_search(session: "AccountSession", profile: Dict[str, Any]) -> bool:
    full_name = profile.get("full_name")
    public_identifier = profile.get("public_identifier")

    # Reconstruct full_name if it's missing
    if not full_name:
        first = profile.get("first_name", "").strip()
        last = profile.get("last_name", "").strip()
        if first or last:
            full_name = f"{first} {last}".strip() if first and last else (first or last)
        else:
            logger.error(f"No name available for {public_identifier}")
            logger.debug(profile)
            return False

    if not public_identifier:
        logger.error(f"Missing public_identifier for '{full_name}'")
        raise ValueError("public_identifier is required")

    logger.info(f"Human search → '{full_name}' (target: {public_identifier})")

    _initiate_search(session, full_name)

    max_pages_to_scan = 1

    for current_page in range(1, max_pages_to_scan + 1):
        logger.info("Scanning search results page %s", current_page)

        target_locator = None
        for link in session.page.locator(SELECTORS["profile_links"]).all():
            href = link.get_attribute("href") or ""
            if f"/in/{public_identifier}" in href:
                target_locator = link
                break

        if target_locator:
            logger.info("Target found in results → clicking")
            return False

        if session.page.get_by_text("No results found", exact=False).count() > 0:
            logger.info("No results found → stopping search")
            break

        if current_page < max_pages_to_scan:
            _paginate_to_next_page(session, current_page + 1)
            session.wait()

    logger.info("Target %s not found → falling back to direct URL", public_identifier)
    return False


# ——————————————————————————————————————————————————————————————
if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(name)s - %(levelname)s - %(message)s"
    )

    if len(sys.argv) != 2:
        print("Usage: python -m linkedin.actions.search <handle>")
        sys.exit(1)

    handle = sys.argv[1]

    from linkedin.sessions.registry import get_session
    session = get_session(handle=handle)

    # Make sure browser is up

    test_profile = {
        "full_name": "Bill Gates",
        "url": "https://www.linkedin.com/in/williamhgates/",
        "public_identifier": "williamhgates",
    }

    search_profile(session, test_profile)

    logger.info("Search complete! Final URL → %s", session.page.url)
    input("Press Enter to close browser...")
    session.close()
