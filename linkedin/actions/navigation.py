# linkedin/actions/navigation.py

import logging
import random
import time
from typing import Dict, Any, Callable
from urllib.parse import urlparse, parse_qs, urlencode

from linkedin.actions.login import PlaywrightResources, get_resources_with_state_management

logger = logging.getLogger(__name__)


class ProfileNotFoundInSearchError(Exception):
    """Custom exception raised when a profile cannot be found via search."""
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


def simulate_human_search(
        resources: PlaywrightResources,
        profile_data: Dict[str, Any]
):
    """
    Simulates a human-like search for the profile without handling exceptions internally.
    Navigates to the profile if found via search results and pagination.
    """
    page = resources.page
    full_name = profile_data.get("full_name")
    linkedin_id = profile_data.get("public_id")
    if not full_name or not linkedin_id:
        raise ValueError("profile_data must contain 'full_name' and 'public_id'")

    logger.info(f"Simulating search for '{full_name}' targeting ID '{linkedin_id}'")

    # Step 1: Ensure on feed
    if "feed/" not in page.url:
        navigate_and_verify(
            resources,
            action=lambda: page.goto("https://www.linkedin.com/feed/?doFeedRefresh=true&nis=true"),
            expected_url_pattern="feed/",
            error_message="Failed to navigate to LinkedIn feed"
        )

    # Step 2: Initiate search
    search_bar_selector = "//input[contains(@placeholder, 'Search')]"
    search_bar = page.locator(search_bar_selector)
    search_bar.click()
    for char in full_name:
        search_bar.press(char)
        time.sleep(random.uniform(0.05, 0.2))

    # Press Enter and verify navigation to search results
    navigate_and_verify(
        resources,
        action=lambda: search_bar.press("Enter"),
        expected_url_pattern="/search/results/",
        error_message="Failed to reach search results page"
    )

    # Step 3: Pagination loop
    max_pages = 10
    page_num = 1
    while page_num <= max_pages:
        logger.info(f"Scanning search results on page {page_num}")

        # Bulk extract potential profile links
        link_locators = page.locator('a[href*="/in/"]')
        links = link_locators.all()
        logger.info(f"Found {len(links)} potential profile links.")

        for idx, link in enumerate(links):
            href = link.get_attribute("href")
            if href:
                # Normalize href (strip query params and trailing slash)
                normalized_href = urlparse(href).path.rstrip('/')
                if f"/in/{linkedin_id}" in normalized_href:
                    logger.info(f"Found matching profile: {href}")
                    # Click and verify navigation to profile
                    navigate_and_verify(
                        resources,
                        action=lambda: link.click(),
                        expected_url_pattern=linkedin_id,
                        error_message="Failed to navigate to the target profile"
                    )
                    return  # Success

        # Check for "No results found"
        no_results_locator = page.get_by_text("No results found")
        if no_results_locator.count() > 0:
            logger.info("No more results found. Ending search.")
            break

        # Paginate via URL
        current_url = page.url
        parsed_url = urlparse(current_url)
        query_params = parse_qs(parsed_url.query)
        query_params['page'] = [str(page_num + 1)]
        new_query = urlencode(query_params, doseq=True)
        new_url = parsed_url._replace(query=new_query).geturl()
        logger.info(f"Paginating to next page via URL: {new_url}")

        # Goto new URL and verify still on search results
        navigate_and_verify(
            resources,
            action=lambda: page.goto(new_url),
            expected_url_pattern="/search/results/",
            error_message="Failed to paginate to next search results page"
        )

        page_num += 1

    raise ProfileNotFoundInSearchError(f"Could not find profile for ID '{linkedin_id}' after {page_num - 1} pages.")


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
        "full_name": "Bill Gates",
        "linkedin_url": "https://www.linkedin.com/in/williamhgates/",
        "public_id": "williamhgates",
    }

    resources = None
    try:
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