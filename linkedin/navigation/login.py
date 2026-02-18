# linkedin/navigation/login.py
import logging
from pathlib import Path

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
from termcolor import colored

from linkedin.navigation.utils import goto_page, human_type

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


def build_playwright(storage_state=None):
    logger.debug("Launching Playwright")
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=False, slow_mo=200)
    context = browser.new_context(storage_state=storage_state)
    Stealth().apply_stealth_sync(context)
    page = context.new_page()
    return page, context, browser, playwright


def init_playwright_session(session: "AccountSession", handle: str):
    logger.debug("Configuring browser for @%s", handle)
    config = session.account_cfg
    state_file = Path(config["cookie_file"])

    storage_state = str(state_file) if state_file.exists() else None
    if storage_state:
        logger.info("Loading saved session for @%s", handle)

    session.page, session.context, session.browser, session.playwright = build_playwright(storage_state=storage_state)

    if not storage_state:
        playwright_login(session)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        session.context.storage_state(path=str(state_file))
        logger.info(colored("Login successful – session saved", "green", attrs=["bold"]) + f" → {state_file}")
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
        print("Usage: python -m linkedin.navigation.login <handle>")
        sys.exit(1)

    handle = sys.argv[1]

    from linkedin.sessions.registry import get_session
    session = get_session(handle=handle)

    session.ensure_browser()

    init_playwright_session(session=session, handle=handle)
    print("Logged in! Close browser manually.")
    session.page.pause()
