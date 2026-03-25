# linkedin/actions/search.py

import logging
from typing import Dict, Any
from urllib.parse import urlparse, parse_qs, urlencode

from linkedin.browser.nav import goto_page, human_type

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
    """Search LinkedIn People by keyword and navigate to the given page."""
    from linkedin.browser.nav import extract_in_urls
    from linkedin.db.leads import discover_and_enrich

    session.ensure_browser()
    _initiate_search(session, keyword)
    if page > 1:
        _paginate_to_next_page(session, page)

    urls = extract_in_urls(session.page)
    discover_and_enrich(session, urls)


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
    from linkedin.browser.registry import cli_parser, cli_session

    parser = cli_parser("Navigate to a LinkedIn profile")
    parser.add_argument("--profile", required=True, help="Public identifier of the target profile")
    args = parser.parse_args()
    session = cli_session(args)

    test_profile = {
        "url": f"https://www.linkedin.com/in/{args.profile}/",
        "public_identifier": args.profile,
    }

    print(f"Navigating to profile as {session} → {args.profile}")

    search_profile(session, test_profile)

    logger.info("Search complete! Final URL → %s", session.page.url)
    input("Press Enter to close browser...")
    session.close()
