# linkedin/browser/login.py
import logging

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
from termcolor import colored

from linkedin.browser.nav import goto_page, human_type

logger = logging.getLogger(__name__)

LINKEDIN_LOGIN_URL = "https://www.linkedin.com/login"
LINKEDIN_FEED_URL = "https://www.linkedin.com/feed/"

SELECTORS = {
    "email": 'input#username',
    "password": 'input#password',
    "submit": 'button[type="submit"]',
}


def playwright_login(session: "AccountSession"):
    page = session.page
    config = session.account_cfg
    logger.info(colored("Fresh login sequence starting", "cyan") + f" for @{session.handle}")

    goto_page(
        session,
        action=lambda: page.goto(LINKEDIN_LOGIN_URL),
        expected_url_pattern="/login",
        error_message="Failed to load login page",
    )

    human_type(page.locator(SELECTORS["email"]), config["username"])
    session.wait()
    human_type(page.locator(SELECTORS["password"]), config["password"])
    session.wait()

    goto_page(
        session,
        action=lambda: page.locator(SELECTORS["submit"]).click(),
        expected_url_pattern="/feed",
        timeout=40_000,
        error_message="Login failed – no redirect to feed",
    )


def launch_browser(storage_state=None):
    logger.debug("Launching Playwright")
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=False, slow_mo=200)
    context = browser.new_context(storage_state=storage_state)
    Stealth().apply_stealth_sync(context)
    page = context.new_page()
    return page, context, browser, playwright


def _save_cookies(session):
    """Persist Playwright storage state (cookies) to the DB."""
    state = session.context.storage_state()
    session.linkedin_profile.cookie_data = state
    session.linkedin_profile.save(update_fields=["cookie_data"])


def start_browser_session(session: "AccountSession", handle: str):
    logger.debug("Configuring browser for @%s", handle)

    session.linkedin_profile.refresh_from_db(fields=["cookie_data"])
    cookie_data = session.linkedin_profile.cookie_data

    storage_state = cookie_data if cookie_data else None
    if storage_state:
        logger.info("Loading saved session for @%s", handle)

    session.page, session.context, session.browser, session.playwright = launch_browser(storage_state=storage_state)

    if not storage_state:
        playwright_login(session)
        _save_cookies(session)
        logger.info(colored("Login successful – session saved", "green", attrs=["bold"]))
    else:
        goto_page(
            session,
            action=lambda: session.page.goto(LINKEDIN_FEED_URL),
            expected_url_pattern="/feed",
            timeout=30_000,
            error_message="Saved session invalid",
        )

    session.page.wait_for_load_state("load")
    logger.info(colored("Browser ready", "green", attrs=["bold"]))


if __name__ == "__main__":
    import sys

    logging.getLogger().handlers.clear()
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(levelname)-8s │ %(message)s',
    )

    if len(sys.argv) != 2:
        print("Usage: python -m linkedin.browser.login <handle>")
        sys.exit(1)

    handle = sys.argv[1]

    from linkedin.browser.registry import get_or_create_session
    session = get_or_create_session(handle=handle)

    session.ensure_browser()

    start_browser_session(session=session, handle=handle)
    print("Logged in! Close browser manually.")
    session.page.pause()
