import logging
import random
import time
from typing import Callable

from linkedin.navigation.login import PlaywrightResources

logger = logging.getLogger(__name__)


def wait(
        resources: PlaywrightResources,
        min_sleep: float = 1,
        max_sleep: float = 3,
):
    """Introduces a random sleep to simulate human-like behavior and avoid detection, after waiting for page load."""
    time.sleep(random.uniform(min_sleep, max_sleep))
    resources.page.wait_for_load_state("load")


def navigate_and_verify(
        resources: PlaywrightResources,
        action: Callable[[], None],
        expected_url_pattern: str,
        timeout: int = 30000,
        error_message: str = "Navigation verification failed"
):
    """
    Performs a navigation action, waits for the URL to match the expected pattern,
    and verifies after a human-like pause. Raises RuntimeError on failure.

    :param resources: PlaywrightResources containing the page.
    :param action: A callable that performs the navigation (e.g., page.goto or element.click).
    :param expected_url_pattern: Substring or pattern expected in the final URL.
    :param timeout: Timeout in ms for wait_for_url.
    :param post_wait_delay_min: Min seconds for random post-navigation pause.
    :param post_wait_delay_max: Max seconds for random post-navigation pause.
    :param error_message: Custom error message prefix for failure.
    """
    page = resources.page
    try:
        action()
        page.wait_for_url(lambda url: expected_url_pattern in url, timeout=timeout)
        wait(resources)
        if expected_url_pattern not in page.url:
            raise RuntimeError(f"{error_message}: Expected '{expected_url_pattern}' in URL, got '{page.url}'")
        logger.info(f"Navigation successful: Verified URL contains '{expected_url_pattern}'")
    except Exception as e:
        raise RuntimeError(f"{error_message}: {str(e)}") from e
