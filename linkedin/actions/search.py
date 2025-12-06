# linkedin/actions/search.py

import logging
from typing import Dict, Any
from urllib.parse import urlparse, parse_qs, urlencode

from linkedin.navigation.utils import wait, goto_page, PlaywrightResources
from linkedin.sessions import AccountSessionRegistry, AccountSession

logger = logging.getLogger(__name__)


def _go_to_profile(resources: PlaywrightResources, url: str, public_identifier: str):
    """
    Go to profile URL only if we're not already on it.
    Checks by public_identifier — the most reliable way on LinkedIn.
    """
    current_url = resources.page.url
    if f"/in/{public_identifier}" in current_url:
        logger.info(f"Already on profile {public_identifier} → skipping navigation")
        return

    logger.info(f"Navigating to profile: {public_identifier}")
    goto_page(
        resources,
        action=lambda: resources.page.goto(url),
        expected_url_pattern=f"/in/{public_identifier}",
        error_message="Failed to navigate to the target profile"
    )


def search_profile(account_session: AccountSession, profile: Dict[str, Any]):
    resources = account_session.resources
    url = profile.get("url")
    public_id = profile.get("public_identifier")

    if not url or not public_id:
        raise ValueError("Profile must have 'url' and 'public_identifier'")

    if not _simulate_human_search(account_session, profile):
        logger.warning("Search failed, falling back to direct")
        _go_to_profile(resources, url, public_id)


def _initiate_search(resources: PlaywrightResources, full_name: str):
    """Ensures we are on the feed and initiates a search for the given name."""
    page = resources.page
    if "feed/" not in page.url:
        goto_page(
            resources,
            action=lambda: page.goto("https://www.linkedin.com/feed/?doFeedRefresh=true&nis=true"),
            expected_url_pattern="feed/",
            error_message="Failed to navigate to LinkedIn feed"
        )

    search_bar_selector = "//input[contains(@placeholder, 'Search')]"
    search_bar = page.locator(search_bar_selector)
    search_bar.click()
    search_bar.type(full_name, delay=150)

    goto_page(
        resources,
        action=lambda: search_bar.press("Enter"),
        expected_url_pattern="/search/results/",
        error_message="Failed to reach search results page"
    )

    current_url = page.url
    parsed_url = urlparse(current_url)
    path = parsed_url.path.replace('/all/', '/people/') if '/all/' in parsed_url.path else '/search/results/people/'
    query_params = parse_qs(parsed_url.query)
    query_params['page'] = ['1']
    new_query = urlencode(query_params, doseq=True)
    new_url = parsed_url._replace(path=path, query=new_query).geturl()

    goto_page(
        resources,
        action=lambda: page.goto(new_url),
        expected_url_pattern="/search/results/people/",
        error_message="Failed to reach people search results page"
    )


def _paginate_to_next_page(resources: PlaywrightResources, page_num: int):
    """Navigates to the next page of search results."""
    page = resources.page
    current_url = page.url
    parsed_url = urlparse(current_url)
    query_params = parse_qs(parsed_url.query)
    query_params['page'] = [str(page_num)]
    new_query = urlencode(query_params, doseq=True)
    new_url = parsed_url._replace(query=new_query).geturl()

    logger.info(f"Paginating to page {page_num}: {new_url}")
    goto_page(
        resources,
        action=lambda: page.goto(new_url),
        expected_url_pattern="/search/results/",
        error_message="Failed to paginate to next search results page"
    )


def _simulate_human_search(
        account_session: AccountSession,
        profile: Dict[str, Any],
):
    """
    Simulates a search, scrapes all profiles from results, and navigates to the target.
    """
    resources = account_session.resources
    page = resources.page
    full_name = profile.get("full_name")
    linkedin_id = profile.get("public_identifier")
    if not full_name or not linkedin_id:
        raise ValueError("profile must contain 'full_name' and 'public_identifier'")

    logger.info(f"Starting search for '{full_name}' (ID: {linkedin_id})")
    _initiate_search(resources, full_name)

    max_pages = 1
    for page_num in range(1, max_pages):
        logger.info(f"Scanning search results on page {page_num}")

        target_link_locator = None

        # Find the exact link Locator for the target profile (needed for clicking)
        for link in page.locator('a[href*="/in/"]').all():
            href = link.get_attribute("href") or ""
            if f"/in/{linkedin_id}" in href:
                target_link_locator = link
                break

        if target_link_locator:
            logger.info(f"Found target profile: {target_link_locator.get_attribute('href')}")
            goto_page(
                resources,
                action=lambda: target_link_locator.click(),
                expected_url_pattern=linkedin_id,
                error_message="Failed to navigate to the target profile"
            )
            return True

        if resources.page.get_by_text("No results found").count() > 0:
            logger.info("No more results found. Ending search.")
            break

        if page_num < max_pages:
            _paginate_to_next_page(resources, page_num + 1)

    logger.info(f"Could not find profile for ID '{linkedin_id}' after {max_pages} pages.")
    return False


if __name__ == "__main__":
    import sys
    from linkedin.campaigns.connect_follow_up import INPUT_CSV_PATH

    root_logger = logging.getLogger()
    root_logger.handlers = []
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    if len(sys.argv) != 2:
        print("Usage: python -m linkedin.actions.search <handle>")
        sys.exit(1)
    handle = sys.argv[1]

    account_session = AccountSessionRegistry.get_or_create_from_path(
        handle=handle,
        campaign_name="test_search",
        csv_path=INPUT_CSV_PATH,
    )

    target_profile = {
        "full_name": "Bill Gates",
        "url": "https://www.linkedin.com/in/williamhgates/",
        "public_identifier": "williamhgates",
    }

    wait(account_session.resources)
    search_profile(account_session, target_profile)  # ← now passes real session

    logger.info("search_profile executed successfully.")
    logger.info(f"Final URL: {account_session.resources.page.url}")
