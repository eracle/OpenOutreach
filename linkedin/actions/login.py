# linkedin/login.py # noqa

import logging
import random
import time
from collections import namedtuple

from playwright.sync_api import Error, TimeoutError, sync_playwright
from playwright_stealth import Stealth  # Updated for version 2.0.0 API

from ..conf import LINKEDIN_EMAIL, LINKEDIN_PASSWORD

logger = logging.getLogger(__name__)
"""
Number of seconds used to wait the web page's loading.
"""
WAIT_TIMEOUT = 15

LINKEDIN_LOGIN_URL = "https://www.linkedin.com/login"

# Define a namedtuple to bundle the Playwright resources
PlaywrightResources = namedtuple('PlaywrightResources', ['page', 'context', 'browser', 'playwright'])


def playwright_login(resources):
    """
    Logs in to LinkedIn with human-like behavior.
    :param resources: The PlaywrightResources namedtuple.
    :return: Nothing
    """
    resources.page.goto(LINKEDIN_LOGIN_URL)

    logger.debug("Typing email")
    username_field = get_by_xpath(resources, '//*[@id="username"]')
    username_field.click()
    for char in LINKEDIN_EMAIL:
        username_field.press(char)
        time.sleep(random.uniform(0.05, 0.2))

    logger.debug("Typing password")
    password_field = get_by_xpath(resources, '//*[@id="password"]')
    password_field.click()
    for char in LINKEDIN_PASSWORD:
        password_field.press(char)
        time.sleep(random.uniform(0.05, 0.2))

    time.sleep(random.uniform(0.5, 1.5))

    logger.debug("Clicking submit")
    get_by_xpath(resources, '//*[@type="submit"]').click()


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


def get_by_xpath_or_none(resources, xpath, wait_timeout=None, log=False):
    """
    Get a web element locator through the xpath string passed.
    If a TimeoutError is raised, None is returned.
    :param resources: PlaywrightResources namedtuple to use.
    :param xpath: String containing the xpath.
    :param wait_timeout: optional amount of seconds before TimeoutError is raised, default WAIT_TIMEOUT is used otherwise.
    :param log: Whether to log the exception.
    :return: The locator for the web element or None if nothing found.
    """
    try:
        return get_by_xpath(resources, xpath, wait_timeout=wait_timeout)
    except TimeoutError as e:
        if log:
            logger.info(
                f"Current URL:\n{resources.page.url}\nTimeoutError:\nXPATH: {xpath}\nError:{e}"
            )
        return None
    except Error as e:
        if hasattr(resources.page, "url"):
            logger.warning(f"Current URL:\n{resources.page.url}")
        logger.warning(f"Playwright Error:\nXPATH: {xpath}\nError:{e}")
        return None


def is_security_check(resources):
    return get_by_xpath_or_none(resources, '//h1[contains(text(), "security check")]', 3)


def build_playwright(login=True):
    """
    Builds and returns a stealth-enabled Playwright resources bundled in a PlaywrightResources namedtuple.
    Note: This uses a local headless Chromium instance. For remote setups (e.g., equivalent to Selenium Grid),
    you can replace launch() with connect_over_cdp() or connect() if you have a WebSocket endpoint available.
    After use, remember to call resources.context.close() and resources.browser.close() to clean up resources.
    Updated for playwright-stealth 2.0.0 API changes.
    """
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=False)  # Set to False for testing visibility
    context = browser.new_context()
    stealth = Stealth()
    stealth.apply_stealth_sync(context)  # Apply stealth to the context for sync API
    page = context.new_page()
    resources = PlaywrightResources(page=page, context=context, browser=browser, playwright=playwright)
    if login:
        playwright_login(resources)
    return resources


if __name__ == "__main__":
    # Set up basic logging for testing
    logging.basicConfig(level=logging.DEBUG)

    # Build the page with login
    resources = build_playwright(login=True)

    # Wait a bit after login to observe
    resources.page.wait_for_timeout(5000)

    # Optional: Check if login succeeded
    if get_by_xpath_or_none(resources, '//div[@id="global-nav"]', wait_timeout=10):
        logger.info("Login appears successful (global nav found).")
    else:
        logger.warning("Login may have failed (global nav not found).")

    # Clean up
    resources.context.close()
    resources.browser.close()
    resources.playwright.stop()