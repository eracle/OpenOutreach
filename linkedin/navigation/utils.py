# linkedin/navigation/utils.py
import logging
import random
import time
import urllib.parse
from urllib.parse import unquote, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

# Central delay configuration
HUMAN_DELAY_MIN = 1.5
HUMAN_DELAY_MAX = 2.5


def human_delay():
    delay = random.uniform(HUMAN_DELAY_MIN, HUMAN_DELAY_MAX)
    logger.info(f"Pause: {delay:.2f}s")
    time.sleep(delay)


def wait(session):
    human_delay()
    session.page.wait_for_load_state("load")
    # session.page.wait_for_load_state("domcontentloaded")


def goto_page(session: "AccountSession", action, expected_url_pattern: str, timeout: int = 10_000, error_message: str = ""):
    page = session.page
    try:
        action()
        page.wait_for_url(lambda url: expected_url_pattern in unquote(url), timeout=timeout)
    except PlaywrightTimeoutError:
        pass

    wait(session)

    page_url = unquote(page.url)
    if expected_url_pattern not in page_url:
        raise RuntimeError(f"{error_message}: {expected_url_pattern} not in '{page_url}'")

    logger.info("Navigated → %s", page.url)


def decode_url_path_only(url: str) -> str:
    if not url:
        return url
    parsed = urlparse(url.lower())
    clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    return urllib.parse.unquote(clean)


def _extract_in_urls(session):
    page = session.page
    urls = set()
    for link in page.locator('a[href*="/in/"]').all():
        href = link.get_attribute("href")
        if href and "/in/" in href:
            clean = urllib.parse.urlparse(href)._replace(query="", fragment="").geturl()
            urls.add(clean)
    logger.info(f"Extracted {len(urls)} unique /in/ profiles")
    return urls