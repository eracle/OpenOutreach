# linkedin/navigation/login.py

import logging
from pathlib import Path

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from linkedin.conf import get_account_config
from linkedin.navigation.utils import (
    PlaywrightResources,
    wait,
    navigate_and_verify,
)

logger = logging.getLogger(__name__)

LINKEDIN_LOGIN_URL = "https://www.linkedin.com/login"
LINKEDIN_FEED_URL = "https://www.linkedin.com/feed/"

SELECTORS = {
    "email": 'input#username',
    "password": 'input#password',
    "submit": 'button[type="submit"]',
    "global_nav": 'nav[global-nav="global-nav"]',
    "security_check": 'text=Let’s do a quick security check,Help us keep your account safe',
}


def playwright_login(resources):
    page = resources.page
    config = get_account_config(resources.handle)
    logger.info("Starting LinkedIn login for handle: %s", resources.handle)

    # → Go to login page
    logger.debug("Navigating to LinkedIn login page")
    navigate_and_verify(
        resources=resources,
        action=lambda: page.goto(LINKEDIN_LOGIN_URL),
        expected_url_pattern="/login",
        error_message="Failed to load LinkedIn login page",
    )

    # → Enter email
    logger.debug("Filling email field")
    page.locator(SELECTORS["email"]).type(config["username"], delay=50)
    wait(resources)

    # → Enter password
    logger.debug("Filling password field")
    page.locator(SELECTORS["password"]).type(config["password"], delay=50)
    wait(resources)

    # → Submit form
    logger.debug("Clicking login submit button")
    navigate_and_verify(
        resources=resources,
        action=lambda: page.locator(SELECTORS["submit"]).click(),
        expected_url_pattern="/feed",
        timeout=40_000,
        error_message="Login failed – did not redirect to /feed",
    )


def build_playwright(storage_state=None):
    logger.debug("Launching Playwright with stealth")
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=False, slow_mo=250)
    context = browser.new_context(storage_state=storage_state)
    Stealth().apply_stealth_sync(context)
    page = context.new_page()
    logger.debug("Browser and page created")

    return PlaywrightResources(
        page=page,
        context=context,
        browser=browser,
        playwright=playwright,
        handle=None,
    )


def get_resources_with_state_management(handle: str):
    logger.info("Initializing session for handle: %s", handle)
    config = get_account_config(handle)
    state_file = Path(config["cookie_file"])

    storage = None
    if state_file.exists():
        logger.info("Loading saved session from: %s", state_file)
        storage = str(state_file)
    else:
        logger.info("No valid session found or force_login=True → starting fresh")

    resources = build_playwright(storage_state=storage)
    resources = resources._replace(handle=handle)

    # Need to log in
    logger.info("Not logged in → performing fresh login")
    if not storage:
        playwright_login(resources)
    else:
        navigate_and_verify(
            resources=resources,
            action=lambda: resources.page.goto(LINKEDIN_FEED_URL),
            expected_url_pattern="/feed",
            timeout=40_000,
            error_message="Already logged in – did not go to /feed",
        )

    # Save session if we just logged in
    if not storage:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        resources.context.storage_state(path=str(state_file))
        logger.info("Login successful – session saved to: %s", state_file)

    resources.page.wait_for_load_state("load")
    logger.info("Login flow completed successfully!")
    return resources


if __name__ == "__main__":
    # Clean, beautiful console logging
    logging.getLogger().handlers.clear()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s │ %(levelname)-8s │ %(message)s",
        datefmt="%H:%M:%S",
    )

    import sys

    if len(sys.argv) != 2:
        print("Usage: python -m linkedin.navigation.login <handle>")
        sys.exit(1)

    handle = sys.argv[1]
    try:
        resources = get_resources_with_state_management(handle)
        logger.info("Setup complete – you are fully logged in!")
        logger.info("Browser will remain open. Close it manually when done.")
        resources.page.pause()

    finally:
        # Always clean up
        if 'resources' in locals():
            logger.debug("Closing browser and Playwright")
            resources.context.close()
            resources.browser.close()
            resources.playwright.stop()
