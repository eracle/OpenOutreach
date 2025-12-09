# linkedin/actions/search.py

import logging
from typing import Dict, Any
from urllib.parse import urlparse, parse_qs, urlencode

from linkedin.conf import ENABLE_SCRAPE_IN_WAIT
from linkedin.navigation.utils import goto_page
from linkedin.sessions.registry import AccountSessionRegistry

logger = logging.getLogger(__name__)


def _go_to_profile(session: "AccountSession", url: str, public_identifier: str):
    """
    Go to profile URL only if we're not already on it.
    Checks by public_identifier — most reliable on LinkedIn.
    """
    logger.info(f"Navigating to profile: {public_identifier}")
    goto_page(
        session,
        action=lambda: session.page.goto(url),
        expected_url_pattern=f"/in/{public_identifier}",
        error_message="Failed to navigate to the target profile"
    )


def search_profile(session: "AccountSession", profile: Dict[str, Any]):
    """
    Main entry point.
    Tries human-like search first → falls back to direct navigation.
    """

    url = profile.get("url")
    public_identifier = profile.get("public_identifier")

    if f"/in/{public_identifier}" in session.page.url:
        return

    # Ensure browser is alive before doing anything
    session.ensure_browser()

    search = _simulate_human_search(session, profile) if ENABLE_SCRAPE_IN_WAIT else False
    search or _go_to_profile(session, url, public_identifier)


def _initiate_search(session: "AccountSession", full_name: str):
    """Go to feed and start typing the name in the global search bar."""
    page = session.page

    if "feed/" not in page.url:
        goto_page(
            session,
            action=lambda: page.goto("https://www.linkedin.com/feed/?doFeedRefresh=true&nis=true"),
            expected_url_pattern="feed/",
            error_message="Failed to reach LinkedIn feed"
        )

    search_bar = page.locator("//input[contains(@placeholder, 'Search')]")
    search_bar.click()
    search_bar.type(full_name, delay=120)

    goto_page(
        session,
        action=lambda: search_bar.press("Enter"),
        expected_url_pattern="/search/results/",
        error_message="Failed to reach search results"
    )

    # Force people tab + reset to page 1
    current = urlparse(page.url)
    new_path = "/search/results/people/" if "/all/" in current.path else current.path
    params = parse_qs(current.query)
    params["page"] = ["1"]
    new_url = current._replace(path=new_path, query=urlencode(params, doseq=True)).geturl()

    goto_page(
        session,
        action=lambda: page.goto(new_url),
        expected_url_pattern="/search/results/people/",
        error_message="Failed to switch to People tab"
    )


def _paginate_to_next_page(session: "AccountSession", page_num: int):
    page = session.page
    current = urlparse(page.url)
    params = parse_qs(current.query)
    params["page"] = [str(page_num)]
    new_url = current._replace(query=urlencode(params, doseq=True)).geturl()

    logger.info(f"Going to search results page {page_num}")
    goto_page(
        session,
        action=lambda: page.goto(new_url),
        expected_url_pattern="/search/results/",
        error_message="Pagination failed"
    )


def _simulate_human_search(session: "AccountSession", profile: Dict[str, Any]) -> bool:
    """
    Does a real LinkedIn search for the person's name,
    scans results, and clicks the correct profile if found.
    Returns True if target profile was reached.
    """
    page = session.page
    full_name = profile.get("full_name")
    target_id = profile.get("public_identifier")

    if not full_name or not target_id:
        raise ValueError("Need full_name and public_identifier for human search")

    logger.info(f"Searching for '{full_name}' → looking for /{target_id}")

    _initiate_search(session, full_name)

    max_pages_to_scan = 1

    for current_page in range(1, max_pages_to_scan + 1):
        logger.info(f"Scanning page {current_page} of search results")

        # Look for the exact profile link
        target_locator = None
        for link in page.locator('a[href*="/in/"]').all():
            href = link.get_attribute("href") or ""
            if f"/in/{target_id}" in href:
                target_locator = link
                break

        if target_locator:
            logger.info(f"Found target profile → clicking")
            goto_page(
                session,
                action=lambda: target_locator.click(),
                expected_url_pattern=f"/in/{target_id}",
                error_message="Failed to open target profile from search results"
            )
            return True

        # Stop if no results
        if page.get_by_text("No results found", exact=False).count() > 0:
            logger.info("No results message appeared → stopping")
            break

        # Go to next page if not last
        if current_page < max_pages_to_scan:
            _paginate_to_next_page(session, current_page + 1)
            session.wait()  # extra safety

    logger.info(f"Profile {target_id} not found in first {max_pages_to_scan} pages")
    return False


# ——————————————————————————————————————————————————————————————
if __name__ == "__main__":
    import sys
    from linkedin.campaigns.connect_follow_up import INPUT_CSV_PATH

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    if len(sys.argv) != 2:
        print("Usage: python -m linkedin.actions.search <handle>")
        sys.exit(1)

    handle = sys.argv[1]

    session = AccountSessionRegistry.get_or_create_from_path(
        handle=handle,
        campaign_name="test_search",
        csv_path=INPUT_CSV_PATH,
    )

    # Make sure browser is up
    session.ensure_browser()

    test_profile = {
        "full_name": "Bill Gates",
        "url": "https://www.linkedin.com/in/williamhgates/",
        "public_identifier": "williamhgates",
    }

    search_profile(session, test_profile)

    logger.info("Done! Final URL: %s", session.page.url)
    input("Press Enter to close browser...")
    session.close()
