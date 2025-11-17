# linkedin/actions/navigation.py

import logging
import random
import time
from typing import Dict, Any, Callable
from urllib.parse import urlparse, parse_qs, urlencode

from linkedin.actions.login import PlaywrightResources, get_resources_with_state_management
from linkedin.api.client import PlaywrightLinkedinAPI, AuthenticationError
from linkedin.database import db_manager, save_profile, get_profile as get_profile_from_db

logger = logging.getLogger(__name__)


class ProfileNotFoundInSearchError(Exception):
    """Custom exception raised when a profile cannot be found via search."""
    pass


def log_profile(profile_data: Dict[str, Any]):
    """Logs the scraping of a profile. Skeleton function."""
    pass


def navigate_and_verify(
        resources: PlaywrightResources,
        action: Callable[[], None],
        expected_url_pattern: str,
        timeout: int = 30000,
        post_wait_delay_min: float = 1.0,
        post_wait_delay_max: float = 3.0,
        error_message: str = "Navigation verification failed"
):
    """
    Performs a navigation action, waits for the URL to match the expected pattern,
    and verifies after a human-like pause. Raises RuntimeError on failure.

    :param resources: PlaywrightResources containing the page.
    :param action: A callable that performs the navigation (e.g., page.goto or element.click).
    :param expected_url_pattern: Substring or pattern expected in the final URL.
    :param timeout: Timeout in ms for wait_for_url.
    :param post_wait_delay_min: Min seconds for random post-navigation pause.
    :param post_wait_delay_max: Max seconds for random post-navigation pause.
    :param error_message: Custom error message prefix for failure.
    """
    page = resources.page
    try:
        action()
        page.wait_for_url(lambda url: expected_url_pattern in url, timeout=timeout)
        page.wait_for_load_state("load")
        time.sleep(random.uniform(post_wait_delay_min, post_wait_delay_max))
        if expected_url_pattern not in page.url:
            raise RuntimeError(f"{error_message}: Expected '{expected_url_pattern}' in URL, got '{page.url}'")
        logger.info(f"Navigation successful: Verified URL contains '{expected_url_pattern}'")
    except Exception as e:
        raise RuntimeError(f"{error_message}: {str(e)}") from e


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
    for char in full_name:
        search_bar.press(char)
        time.sleep(random.uniform(0.05, 0.2))

    navigate_and_verify(
        resources,
        action=lambda: search_bar.press("Enter"),
        expected_url_pattern="/search/results/",
        error_message="Failed to reach search results page"
    )


def _fetch_and_save_profile(api: PlaywrightLinkedinAPI, clean_url: str):
    """Fetches profile data using the API and saves it to the database."""
    logger.info(f"Scraping new profile: {clean_url}")
    session = db_manager.get_session()
    try:
        # Introduce a random delay before fetching the profile
        delay = random.uniform(2, 5)
        logger.info(f"Pausing for {delay:.2f} seconds before fetching profile: {clean_url}")
        time.sleep(delay)

        profile_data = api.get_profile(profile_url=clean_url)
        if profile_data:
            save_profile(session, profile_data, clean_url)
            log_profile(profile_data)
            logger.info(f"Successfully scraped and saved profile: {clean_url}")
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


def simulate_human_search(
        resources: PlaywrightResources,
        profile_data: Dict[str, Any]
):
    """
    Simulates a search, scrapes all profiles from results, and navigates to the target.
    """
    full_name = profile_data.get("full_name")
    linkedin_id = profile_data.get("public_id")
    if not full_name or not linkedin_id:
        raise ValueError("profile_data must contain 'full_name' and 'public_id'")

    logger.info(f"Starting search for '{full_name}' (ID: {linkedin_id})")
    _initiate_search(resources, full_name)

    max_pages = 3
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


def go_to_profile(
        resources: PlaywrightResources,
        profile_data: Dict[str, Any]
):
    """
    Orchestrates navigating to the profile, using simulated search with fallback to direct URL.
    """
    try:
        simulate_human_search(resources, profile_data)
    except Exception as e:
        logger.warning(f"Simulated search failed: {e}. Falling back to direct navigation.")
        linkedin_url = profile_data.get("linkedin_url")
        linkedin_id = profile_data.get("public_id")
        navigate_and_verify(
            resources,
            action=lambda: resources.page.goto(linkedin_url),
            expected_url_pattern=linkedin_id,
            error_message="Failed to navigate directly to the target profile"
        )


if __name__ == "__main__":
    # Forcefully reset and configure logging to ensure reliability
    root_logger = logging.getLogger()
    root_logger.handlers = []  # Clear any existing handlers to avoid conflicts
    logging.basicConfig(
        level=logging.DEBUG,  # Set to DEBUG for more logs; change to INFO if too verbose
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Example profile data for testing
    bill_gates_profile = {
        "full_name": "Mario Rossi",
        "linkedin_url": "https://www.linkedin.com/in/mariorossi7/",
        "public_id": "mariorossi7",
    }

    resources = None
    try:
        # Set up database
        db_manager.init_db('sqlite:///linkedin.db')
        db_manager.create_tables()

        # Get resources with state management
        resources = get_resources_with_state_management(use_state=True, force_login=False)

        # Wait a bit after setup to observe
        resources.page.wait_for_load_state('load')

        # Test the end-to-end function
        go_to_profile(resources, bill_gates_profile)

        logger.info("go_to_profile function executed successfully.")
        logger.info(f"Final URL: {resources.page.url}")

    except ProfileNotFoundInSearchError as e:
        logger.error(f"Test failed: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred during the test: {e}")
    finally:
        if resources:
            logger.info("Cleaning up Playwright resources.")
            time.sleep(5)  # Keep browser open for a bit to see the result
            resources.context.close()
            resources.browser.close()
            resources.playwright.stop()
