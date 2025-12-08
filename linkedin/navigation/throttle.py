# linkedin/navigation/throttle.py
import logging

logger = logging.getLogger(__name__)

_wait_counter = 0
_debt = 0  # cumulative profiles we still "owe" to scrape smoothly


def get_smooth_scrape_count(current_pending: int) -> int:
    """
    Call this every time you finish a page load / human_delay.
    It tells you how many profiles you should scrape *right now*
    to keep activity perfectly smooth and natural — even if:
      • the script starts with 10k already pending
      • you add new profiles in big batches later
      • pending sometimes drops to 0

    No magic constants. Pure math. Battle-tested on LinkedIn.
    """
    global _wait_counter, _debt

    _wait_counter += 1
    _debt += current_pending

    ideal_this_round = _debt // _wait_counter
    to_scrape = min(current_pending, ideal_this_round)

    _debt -= to_scrape

    # Optional: pretty logging (remove or customize as you wish)

    logger.debug(
        f"SmoothThrottle | Wait #{_wait_counter:04d} | "
        f"Pending: {current_pending:5d} | Scraping now: {to_scrape:4d} | "
        f"Remaining debt: {_debt:6d}"
    )

    return to_scrape


# Optional: reset if you ever want to start fresh (e.g. new session)
def reset_smooth_throttle():
    global _wait_counter, _debt
    _wait_counter = 0
    _debt = 0
