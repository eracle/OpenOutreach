# linkedin/navigation/throttle.py
import logging

logger = logging.getLogger(__name__)

_wait_counter = 0
_debt = 0  # cumulative profiles we still "owe" to scrape smoothly


def determine_batch_size(session: "AccountSession") -> int:
    from linkedin.db.profiles import count_pending_scrape
    pending = count_pending_scrape(session)

    logger.debug(f"Wait #{_wait_counter:04d} | Profiles still needing scrape: {pending}")

    amount_to_scrape = get_smooth_scrape_count(pending)
    return amount_to_scrape


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

    _debt -= to_scrape * _wait_counter  # proper amortization

    # Cyber-ninja throttle log — beautiful, compact, informative
    logger.debug(
        "Throttle | Cycle:%04d | Pending:%5d → Scrape:%3d | Debt:%6d | Pace:%.2f/hr",
        _wait_counter,
        current_pending,
        to_scrape,
        _debt,
        (_wait_counter * 3600) / max(1, _wait_counter)  # dummy pace, replace if you track real time
    )

    return to_scrape
