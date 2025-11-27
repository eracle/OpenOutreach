# linkedin/actions/search.py

import logging
from typing import Dict, Any
from urllib.parse import urlparse, parse_qs, urlencode

from linkedin.api.client import PlaywrightLinkedinAPI
from linkedin.db.engine import save_profile, get_profile
from linkedin.navigation.errors import ProfileNotFoundInSearchError, AuthenticationError
from linkedin.navigation.utils import wait, navigate_and_verify, human_delay, PlaywrightResources

logger = logging.getLogger(__name__)


def search_profile(automation: "LinkedInAutomation", profile: Dict[str, Any]):
    resources = automation.browser
    session = automation.db.get_session()

    linkedin_url = profile.get("linkedin_url")

    try:
        _simulate_human_search(resources, profile, session)
    except Exception as e:
        logger.warning(f"Simulated search failed: {e}. Falling back to direct navigation.")
        navigate_and_verify(
            resources,
            action=lambda: resources.page.goto(linkedin_url),
            expected_url_pattern=linkedin_url,
            error_message="Failed to navigate directly to the target profile"
        )


def _initiate_search(resources: PlaywrightResources, full_name: str):
    """Ensures we are on the feed and initiates a search for the given name."""
    page = resources.page
    if "feed/" not in page.url:
        navigate_and_verify(
            resources,
            action=lambda: page.goto("https://www.linkedin.com/feed/?doFeedRefresh=true&nis=true"),
            expected_url_pattern="feed/",
            error_message="Failed to navigate to LinkedIn feed"
        )

    search_bar_selector = "//input[contains(@placeholder, 'Search')]"
    search_bar = page.locator(search_bar_selector)
    search_bar.click()
    search_bar.type(full_name, delay=150)

    navigate_and_verify(
        resources,
        action=lambda: search_bar.press("Enter"),
        expected_url_pattern="/search/results/",
        error_message="Failed to reach search results page"
    )

    # After pressing Enter, modify the URL to switch to people results and add page=1
    current_url = page.url
    parsed_url = urlparse(current_url)
    path = parsed_url.path.replace('/all/', '/people/') if '/all/' in parsed_url.path else '/search/results/people/'
    query_params = parse_qs(parsed_url.query)
    query_params['page'] = ['1']
    new_query = urlencode(query_params, doseq=True)
    new_url = parsed_url._replace(path=path, query=new_query).geturl()

    navigate_and_verify(
        resources,
        action=lambda: page.goto(new_url),
        expected_url_pattern="/search/results/people/",
        error_message="Failed to reach people search results page"
    )


def _process_search_results_page(resources: PlaywrightResources, target_linkedin_id: str, session):
    """Processes a page of search results, scraping each profile only once."""
    page = resources.page
    link_locators = page.locator('a[href*="/in/"]').all()
    logger.info(f"Found {len(link_locators)} potential profile links.")

    api = PlaywrightLinkedinAPI(resources=resources)
    target_link_locator = None

    # Step 1: Collect and clean all URLs, remove duplicates
    unique_clean_urls = set()

    for link in link_locators:
        href = link.get_attribute("href")
        if not href:
            continue

        # Simple but effective cleaning
        parsed = urlparse(href)
        clean_url = parsed._replace(query="", fragment="").geturl()

        # Extra safety: ensure it's really a /in/ profile
        if "/in/" in clean_url:
            unique_clean_urls.add(clean_url)

        # Check for target (use original href to match locator)
        if f"/in/{target_linkedin_id}" in href:
            target_link_locator = link

    logger.info(f"After deduplication: {len(unique_clean_urls)} unique profiles to process.")

    # Step 2: Process only unique URLs
    try:
        for clean_url in unique_clean_urls:
            if not get_profile(session, clean_url):
                human_delay()
                parsed_profile, raw_json = api.get_profile(profile_url=clean_url)
                save_profile(session, parsed_profile, raw_json, clean_url)
            else:
                logger.info(f"Already in DB, skipping: {clean_url}")
    except AuthenticationError:
        logger.warning("Authentication error. Stopping scraping for this page.")

    return target_link_locator


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
    navigate_and_verify(
        resources,
        action=lambda: page.goto(new_url),
        expected_url_pattern="/search/results/",
        error_message="Failed to paginate to next search results page"
    )


def _simulate_human_search(
        resources: PlaywrightResources,
        profile: Dict[str, Any],
        session
):
    """
    Simulates a search, scrapes all profiles from results, and navigates to the target.
    """
    full_name = profile.get("full_name")
    linkedin_id = profile.get("public_id")
    if not full_name or not linkedin_id:
        raise ValueError("profile must contain 'full_name' and 'public_id'")

    logger.info(f"Starting search for '{full_name}' (ID: {linkedin_id})")
    _initiate_search(resources, full_name)

    max_pages = 2
    for page_num in range(1, max_pages + 1):
        logger.info(f"Scanning search results on page {page_num}")

        target_link = _process_search_results_page(resources, linkedin_id, session)

        if target_link:
            logger.info(f"Found target profile: {target_link.get_attribute('href')}")
            navigate_and_verify(
                resources,
                action=lambda: target_link.click(),
                expected_url_pattern=linkedin_id,
                error_message="Failed to navigate to the target profile"
            )
            return  # Success

        if resources.page.get_by_text("No results found").count() > 0:
            logger.info("No more results found. Ending search.")
            break

        if page_num < max_pages:
            _paginate_to_next_page(resources, page_num + 1)

    raise ProfileNotFoundInSearchError(f"Could not find profile for ID '{linkedin_id}' after {max_pages} pages.")


if __name__ == "__main__":
    import sys
    from pathlib import Path
    from linkedin.automation import AutomationRegistry

    # Forcefully reset and configure logging (unchanged)
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

    # ← ONLY THIS BLOCK CHANGED – use the registry instead of manual resources/db
    automation = AutomationRegistry.get_or_create(
        handle=handle,
        campaign_name="test_search",
        csv_hash="debug",
        input_csv=Path("dummy.csv"),
    )

    # Same test profile as before
    target_profile = {
        "full_name": "Bill Gates",
        "linkedin_url": "https://www.linkedin.com/in/williamhgates/",
        "public_id": "williamhgates",
    }

    try:
        wait(automation.browser)  # ← use automation.browser
        search_profile(automation, target_profile)  # ← pass automation

        logger.info("search_to_profile function executed successfully.")
        logger.info(f"Final URL: {automation.browser.page.url}")
    except Exception as e:
        logger.exception(e)
        raise
