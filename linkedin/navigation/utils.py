import logging
import random
import time
from typing import Callable

from linkedin.navigation.login import PlaywrightResources

logger = logging.getLogger(__name__)

# Central delay configuration
HUMAN_DELAY_MIN = 1
HUMAN_DELAY_MAX = 3


def human_delay(min_sec: float = HUMAN_DELAY_MIN, max_sec: float = HUMAN_DELAY_MAX) -> None:
    """Pure human-like random delay."""
    delay = random.uniform(min_sec, max_sec)
    logger.info(f"Pausing for {delay:.2f} seconds.")
    time.sleep(delay)


def wait(resources: PlaywrightResources) -> None:
    """Legacy wrapper: human delay + wait for page load."""
    human_delay()
    resources.page.wait_for_load_state("load")


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
            lambda url: expected_url_pattern in (url or ""),
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
