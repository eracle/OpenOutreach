# linkedin/actions/search.py

import logging
import random
import time
from typing import Dict, Any
from urllib.parse import urlparse, parse_qs, urlencode

from linkedin.api.client import PlaywrightLinkedinAPI
from linkedin.api.logging import log_profile
from linkedin.database import db_manager, save_profile, get_profile as get_profile_from_db
from linkedin.navigation.errors import ProfileNotFoundInSearchError, AuthenticationError
from linkedin.navigation.login import PlaywrightResources, get_resources_with_state_management
from linkedin.navigation.utils import wait, navigate_and_verify

logger = logging.getLogger(__name__)


def search_to_profile(resources: PlaywrightResources, profile: Dict[str, Any]):
    """
    Orchestrates navigating to the profile, using simulated search with fallback to direct URL.
    If direct is True, navigates directly to the profile URL without attempting search.
    """
    linkedin_url = profile.get("linkedin_url")
    linkedin_id = profile.get("public_id")

    try:
        _simulate_human_search(resources, profile)
    except Exception as e:
        logger.warning(f"Simulated search failed: {e}. Falling back to direct navigation.")
        navigate_and_verify(
            resources,
            action=lambda: resources.page.goto(linkedin_url),
            expected_url_pattern=linkedin_id,
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


def _fetch_and_save_profile(api: PlaywrightLinkedinAPI, clean_url: str):
    """Fetches profile data using the API and saves it to the database."""
    logger.info(f"Scraping new profile: {clean_url}")
    session = db_manager.get_session()
    try:
        # Introduce a random delay before fetching the profile
        delay = random.uniform(1, 3)
        logger.info(f"Pausing for {delay:.2f} seconds before fetching profile: {clean_url}")
        time.sleep(delay)

        profile, get_profile_json = api.get_profile(profile_url=clean_url)
        if profile:
            save_profile(session, profile, clean_url)
            log_profile(profile, get_profile_json)
            logger.info(f"Successfully scraped and saved profile: {profile['full_name']}")
        else:
            logger.warning(f"Could not retrieve data for profile: {clean_url}")
    except AuthenticationError:
        raise  # Re-raise to be handled by the page processing loop
    except Exception as e:
        logger.error(f"Failed to scrape profile {clean_url}: {e}")


def _scrape_profile_if_new(api: PlaywrightLinkedinAPI, profile_url: str):
    """Checks if a profile is in the database and scrapes it if it's new."""
    session = db_manager.get_session()
    parsed_url = urlparse(profile_url)
    clean_url = parsed_url._replace(query="", fragment="").geturl()

    if not get_profile_from_db(session, clean_url):
        _fetch_and_save_profile(api, clean_url)
    else:
        logger.info(f"Profile already in database: {clean_url}")


def _process_search_results_page(resources: PlaywrightResources, target_linkedin_id: str):
    """Processes a page of search results, scraping profiles and finding the target."""
    page = resources.page
    link_locators = page.locator('a[href*="/in/"]').all()
    logger.info(f"Found {len(link_locators)} potential profile links.")

    api = PlaywrightLinkedinAPI(resources=resources)
    target_link_locator = None
    scraping_this_page_enabled = True

    for link in link_locators:
        href = link.get_attribute("href")
        if href:
            if scraping_this_page_enabled:
                try:
                    _scrape_profile_if_new(api, href)
                except AuthenticationError:
                    logger.warning("Authentication error on this page. Disabling further scraping for this page only.")
                    scraping_this_page_enabled = False

            if f"/in/{target_linkedin_id}" in href:
                target_link_locator = link

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
        profile: Dict[str, Any]
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

        target_link = _process_search_results_page(resources, linkedin_id)

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
    # Forcefully reset and configure logging to ensure reliability
    root_logger = logging.getLogger()
    root_logger.handlers = []  # Clear any existing handlers to avoid conflicts
    logging.basicConfig(
        level=logging.DEBUG,  # Set to DEBUG for more logs; change to INFO if too verbose
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Example profile data for testing
    target_profile = {
        "full_name": "Bill Gates",
        "linkedin_url": "https://www.linkedin.com/in/williamhgates/",
        "public_id": "williamhgates",
    }

    # CHANGE 1: accept handle from command line
    import sys
    if len(sys.argv) != 2:
        print("Usage: python -m linkedin.actions.search <handle>")
        sys.exit(1)
    handle = sys.argv[1]

    resources = None
    try:
        # Set up database
        db_manager.init_db('sqlite:///linkedin.db')
        db_manager.create_tables()

        # CHANGE 2: pass the handle here
        resources = get_resources_with_state_management(handle, use_state=True, force_login=False)

        # Wait a bit after setup to observe
        wait(resources)

        # Test the end-to-end function
        search_to_profile(resources, target_profile)

        logger.info("go_to_profile function executed successfully.")
        logger.info(f"Final URL: {resources.page.url}")

    except Exception as e:
        logger.error(f"An unexpected error occurred during the test: {e}")
    finally:
        if resources:
            logger.info("Cleaning up Playwright resources.")
            resources.context.close()
            resources.browser.close()
            resources.playwright.stop()