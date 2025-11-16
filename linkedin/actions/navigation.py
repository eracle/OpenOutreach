# linkedin/actions/navigation.py

import logging
import time
from typing import Dict, Any

from playwright.sync_api import Page, TimeoutError

from linkedin.actions.login import build_playwright, PlaywrightResources

logger = logging.getLogger(__name__)


class ProfileNotFoundInSearchError(Exception):
    """Custom exception raised when a profile cannot be found via search."""
    pass


def go_to_profile_search_page(
    resources: PlaywrightResources,
    full_name: str
):
    """
    Navigates to the LinkedIn general search results page for a given name.
    """
    page = resources.page
    
    # Directly navigate to the LinkedIn feed URL as previous selectors for the home button were unreliable.
    page.goto("https://www.linkedin.com/feed/?doFeedRefresh=true&nis=true")
    page.wait_for_load_state("networkidle")
    logger.info("Navigated to LinkedIn feed using page.goto().")


    search_bar_selector = "//input[contains(@placeholder, 'Search')]"
    
    logger.info(f"Searching for: '{full_name}'")
    
    try:
        search_bar = page.locator(search_bar_selector)
        search_bar.click()
        search_bar.fill(full_name)
        search_bar.press("Enter")
        
        logger.info("Pressed Enter to navigate to search results page.")
        page.wait_for_load_state("networkidle")
        
        logger.info(f"Successfully navigated to general search results page: {page.url}")

    except TimeoutError as e:
        logger.error(f"A timeout occurred during the search navigation: {e}")
        raise
    except Exception as e:
        logger.error(f"An unexpected error occurred during search navigation: {e}")
        raise


def find_profile_in_search_results(
    resources: PlaywrightResources,
    profile_data: Dict[str, Any],
    see_all_people_text: str = "See all people results",
    next_button_text: str = "Next"
):
    """
    Finds and navigates to a profile from the search results page, handling pagination.
    """
    page = resources.page
    target_url = profile_data.get("linkedin_url")
    if not target_url:
        raise ValueError("profile_data must contain a 'linkedin_url'")

    try:
        # Click on "See all people results" to filter for people
        see_all_button_selector = f"//button[text()='{see_all_people_text}']"
        page.locator(see_all_button_selector).click()
        
        logger.info(f"Clicked on '{see_all_people_text}'.")
        page.wait_for_load_state("networkidle")
    except TimeoutError:
        logger.info("'See all people results' button not found, assuming we are already on a people results page.")


    page_limit = 10  # To prevent infinite loops
    for page_num in range(page_limit):
        logger.info(f"Checking search results on page {page_num + 1}...")

        list_selector = "ul[role='list']"
        page.wait_for_selector(list_selector, timeout=10000)
        
        list_items = page.locator(f"{list_selector} > li").all()
        logger.info(f"Found {len(list_items)} results on this page.")

        for item in list_items:
            # Find the link within the list item
            link_locator = item.locator("a[href*='/in/']")
            if link_locator.count() > 0:
                href = link_locator.first.get_attribute("href")
                # Normalize URL for comparison
                if href and target_url in href:
                    logger.info(f"Found matching profile URL: {href}")
                    link_locator.first.click()
                    page.wait_for_load_state("networkidle")
                    logger.info(f"Successfully navigated to profile page: {page.url}")
                    return  # Success

        # Handle pagination
        next_button_locator = page.locator(f"//button[text()='{next_button_text}']")
        if next_button_locator.count() > 0 and next_button_locator.is_enabled():
            logger.info("Clicking 'Next' to go to the next page of results.")
            next_button_locator.click()
            page.wait_for_load_state("networkidle")
        else:
            logger.info("No 'Next' button found or it's disabled. End of search results.")
            break  # Exit loop if no next page

    raise ProfileNotFoundInSearchError(
        f"Could not find profile for {target_url} after checking all search result pages."
    )


def go_to_profile(
    resources: PlaywrightResources,
    profile_data: Dict[str, Any],
    see_all_people_text: str = "See all people results",
    next_button_text: str = "Next"
):
    """
    Orchestrates the process of searching for a profile and navigating to their page.
    """
    full_name = profile_data.get("full_name")
    if not full_name:
        raise ValueError("profile_data must contain a 'full_name'")

    go_to_profile_search_page(resources, full_name)
    find_profile_in_search_results(resources, profile_data, see_all_people_text, next_button_text)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Example profile data for testing
    bill_gates_profile = {
        "full_name": "Bill Gates",
        "linkedin_url": "https://www.linkedin.com/in/williamhgates/",
    }

    resources = None
    try:
        # Build the page with login
        resources = build_playwright(login=True)

        # Wait a bit after login to observe
        resources.page.wait_for_timeout(3000)

        # TODO: still needs finish testing
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
            time.sleep(5) # Keep browser open for a bit to see the result
            resources.context.close()
            resources.browser.close()
            resources.playwright.stop()
