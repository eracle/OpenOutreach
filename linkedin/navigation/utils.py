# linkedin/navigation/utils.py
import logging
import random
import time
from collections import namedtuple
from typing import Callable

logger = logging.getLogger(__name__)

# Central delay configuration
HUMAN_DELAY_MIN = 0.5
HUMAN_DELAY_MAX = 1.5


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
    resources.page.wait_for_load_state("domcontentloaded")


def navigate_and_verify(
        resources: PlaywrightResources,
        action: Callable[[], None],
        expected_url_pattern: str,
        timeout: int = 30_000,
        error_message: str = "Navigation verification failed",
):
    """Navigate → wait for URL → human pause + load → verify."""
    page = resources.page
    try:
        action()
        page.wait_for_url(
            lambda url: expected_url_pattern in url,
            timeout=timeout,
        )
        wait(resources)  # uses the global delay range

        if expected_url_pattern not in page.url:
            raise RuntimeError(
                f"{error_message}: Expected '{expected_url_pattern}' in URL, got '{page.url}'"
            )
        logger.info("Navigation successful: %s", expected_url_pattern)

    except Exception as e:
        raise RuntimeError(f"{error_message}: {e}") from e
