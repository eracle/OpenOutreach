# linkedin/navigation/utils.py
import logging
import random
import time
import urllib.parse
from collections import namedtuple
from typing import Callable, Set
from urllib.parse import unquote, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from linkedin.actions.search import logger

logger = logging.getLogger(__name__)

# Central delay configuration
HUMAN_DELAY_MIN = 1.5
HUMAN_DELAY_MAX = 2.5


def human_delay(min_sec: float = HUMAN_DELAY_MIN, max_sec: float = HUMAN_DELAY_MAX) -> None:
    """Pure human-like random delay."""
    delay = random.uniform(min_sec, max_sec)
    logger.info(f"Pausing for {delay:.2f} seconds.")
    time.sleep(delay)


PlaywrightResources = namedtuple(
    'PlaywrightResources', ['page', 'context', 'browser', 'playwright']
)


def wait(resources: PlaywrightResources, min_sec: float = HUMAN_DELAY_MIN, max_sec: float = HUMAN_DELAY_MAX) -> None:
    """Legacy wrapper: human delay + wait for page load."""
    human_delay(min_sec, max_sec)
    resources.page.wait_for_load_state("load")
    # resources.page.wait_for_load_state("domcontentloaded")


def goto_page(
        resources: PlaywrightResources,
        action: Callable[[], None],
        expected_url_pattern: str,
        timeout: int = 5_000,
        error_message: str = "Navigation verification failed",
):
    page = resources.page

    try:
        action()
        page.wait_for_url(
            lambda url: expected_url_pattern in unquote(url),
            timeout=timeout,
        )
    except PlaywrightTimeoutError:
        # ignoring the exception here
        # I will check for url matching down below
        pass

    wait(resources)

    page_url = unquote(page.url)
    if expected_url_pattern not in page_url:
        raise RuntimeError(f"{error_message}: {expected_url_pattern} not in '{page_url}'")

    logger.info("Navigation OK → %s", page.url)


def decode_url_path_only(url: str) -> str:
    """
    Decodes a percent-encoded LinkedIn URL and returns only the clean path part
    (removes query parameters, fragments, etc.).

    Example:
      Input : "https://www.linkedin.com/in/%d0%bf%d0%b0%d0%b2%d0%b5%d0%bb-%d1%84%d0%b5%d0%b4%d0%be%d1%81%d0%b5%d0%b5%d0%b2-8364b689?refId=123"
      Output: "https://www.linkedin.com/in/павел-федосеев-8364b689"
    """
    if not url or not isinstance(url, str):
        return url

    parsed = urlparse(url.lower())

    # Take only the scheme + netloc + path (ignore query and fragment)
    clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    # Decode percent-encoding in the path (e.g., %d0%bf → п)
    return urllib.parse.unquote(clean_url)


def _extract_in_urls(resources: PlaywrightResources) -> Set[str]:
    """
    Extract all unique, cleaned /in/ profile URLs from the current page.
    Returns a set of URLs without query parameters or fragments.
    """
    page = resources.page
    unique_clean_urls: Set[str] = set()
    link_locators = page.locator('a[href*="/in/"]').all()

    for link in link_locators:
        href = link.get_attribute("href")
        if not href:
            continue

        parsed = urlparse(href)
        clean_url = parsed._replace(query="", fragment="").geturl()

        if "/in/" in clean_url:
            unique_clean_urls.add(clean_url)

    logger.info(f"Extracted {len(unique_clean_urls)} unique /in/ profile URLs.")
    return unique_clean_urls
