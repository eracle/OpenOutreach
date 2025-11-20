# linkedin/login.py # noqa

import logging
import os
from collections import namedtuple

from playwright.sync_api import TimeoutError, sync_playwright
from playwright_stealth import Stealth  # Updated for version 2.0.0 API

from ..conf import LINKEDIN_EMAIL, LINKEDIN_PASSWORD

logger = logging.getLogger(__name__)
"""
Number of seconds used to wait the web page's loading.
"""
WAIT_TIMEOUT = 15

LINKEDIN_LOGIN_URL = "https://www.linkedin.com/login"
LINKEDIN_FEED_URL = "https://www.linkedin.com/feed/"
GLOBAL_NAV_XPATH = '//div[starts-with(@id, "global-nav")]'
STATE_FILE = "linkedin_state.json"

# Define a namedtuple to bundle the Playwright resources
PlaywrightResources = namedtuple('PlaywrightResources', ['page', 'context', 'browser', 'playwright'])


def playwright_login(resources):
    """
    Logs in to LinkedIn with human-like behavior.
    :param resources: The PlaywrightResources namedtuple.
    :return: Nothing
    """
    resources.page.goto(LINKEDIN_LOGIN_URL)
    resources.page.wait_for_load_state('load')

    logger.debug("Typing email")
    username_field = get_by_xpath(resources, '//*[@id="username"]')
    username_field.click()
    username_field.type(LINKEDIN_EMAIL, delay=150)

    logger.debug("Typing password")
    password_field = get_by_xpath(resources, '//*[@id="password"]')
    password_field.click()
    password_field.type(LINKEDIN_PASSWORD, delay=150)

    logger.debug("Clicking submit")
    with resources.page.expect_navigation():
        get_by_xpath(resources, '//*[@type="submit"]').click()

    # After login attempt, check for security check
    if is_security_check(resources):
        logger.warning("Security check detected. Manual intervention may be required.")
        # Pause for manual resolution if needed (e.g., CAPTCHA)
        input("Press Enter after resolving security check...")


def get_by_xpath(resources, xpath, wait_timeout=None):
    """
    Get a web element locator through the xpath passed by performing a wait on it.
    :param resources: PlaywrightResources namedtuple to use.
    :param xpath: xpath to use.
    :param wait_timeout: optional amount of seconds before TimeoutError is raised, default WAIT_TIMEOUT is used otherwise.
    :return: The locator for the web element.
    """
    if wait_timeout is None:
        wait_timeout = WAIT_TIMEOUT
    selector = f'xpath={xpath}'
    resources.page.wait_for_selector(selector, timeout=wait_timeout * 1000)  # timeout in ms
    return resources.page.locator(selector)


def is_security_check(resources, wait_timeout=3):
    """
    Checks for security check page.
    :param resources: The PlaywrightResources namedtuple.
    :param wait_timeout: Optional timeout in seconds for the check.
    :return: True if security check element found, False otherwise.
    """
    try:
        get_by_xpath(resources, '//h1[contains(text(), "security check")]', wait_timeout)
        return True
    except TimeoutError:
        return False


def is_logged_in(resources, wait_timeout=10):
    """
    Checks if the user is logged in to LinkedIn by looking for the global navigation element.
    :param resources: The PlaywrightResources namedtuple.
    :param wait_timeout: Optional timeout in seconds for the check.
    :return: True if logged in (element found), False otherwise.
    """
    try:
        get_by_xpath(resources, GLOBAL_NAV_XPATH, wait_timeout=wait_timeout)
        return True
    except TimeoutError:
        return False


def build_playwright(storage_state=None):
    """
    Builds and returns a stealth-enabled Playwright resources bundled in a PlaywrightResources namedtuple.
    Optionally loads a provided storage state.
    Note: This uses a local headless Chromium instance. For remote setups (e.g., equivalent to Selenium Grid),
    you can replace launch() with connect_over_cdp() or connect() if you have a WebSocket endpoint available.
    After use, remember to call resources.context.close() and resources.browser.close() to clean up resources.
    Updated for playwright-stealth 2.0.0 API changes.
    :param storage_state: Optional path to storage state file or dict.
    """
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=False, slow_mo=250)  # Set to False for testing visibility
    context = browser.new_context(storage_state=storage_state)
    stealth = Stealth()
    stealth.apply_stealth_sync(context)  # Apply stealth to the context for sync API
    page = context.new_page()
    resources = PlaywrightResources(page=page, context=context, browser=browser, playwright=playwright)
    return resources


def get_resources_with_state_management(use_state=True, force_login=False):
    """
    Gets Playwright resources with state management: loads from local file if present and valid, otherwise logs in and saves state.
    If state is loaded and valid (logged in), skips login. Always navigates to feed page and waits for load.
    :param use_state: Whether to attempt loading/saving state (default: True).
    :param force_login: Force login even if state exists (default: False).
    :return: The PlaywrightResources.
    """
    if use_state and os.path.exists(STATE_FILE) and not force_login:
        logger.info(f"Loading state from {STATE_FILE}.")
        resources = build_playwright(storage_state=STATE_FILE)
    else:
        resources = build_playwright()

    # Navigate to feed to check login status
    resources.page.goto(LINKEDIN_FEED_URL)
    resources.page.wait_for_load_state('load')

    did_login = False
    if not is_logged_in(resources):
        logger.info("Not logged in. Performing login.")
        playwright_login(resources)
        # After login, navigate back to feed
        resources.page.goto(LINKEDIN_FEED_URL)
        resources.page.wait_for_load_state('load')
        did_login = True
    else:
        logger.info("Already logged in via loaded state.")

    if not is_logged_in(resources):
        logger.error("Login failed even after attempt.")
        # You may want to raise an exception or handle failure here
    elif use_state and did_login:
        resources.context.storage_state(path=STATE_FILE)
        logger.info(f"State saved to {STATE_FILE}.")

    return resources


if __name__ == "__main__":
    # Set up basic logging for testing
    logging.basicConfig(level=logging.DEBUG)

    # Get the resources with state management
    resources = get_resources_with_state_management(use_state=True, force_login=False)

    # Wait a bit after setup to observe
    resources.page.wait_for_load_state('load')

    # Optional: Check if login succeeded
    if is_logged_in(resources):
        logger.info("Setup successful (global nav found).")
    else:
        logger.warning("Setup may have failed (global nav not found).")

    # Clean up
    resources.context.close()
    resources.browser.close()
    resources.playwright.stop()
