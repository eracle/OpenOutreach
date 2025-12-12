# linkedin/navigation/throttle.py
import logging

from linkedin.db.profiles import count_pending_scrape

logger = logging.getLogger(__name__)

# Simple state
_state = {
    "wait_count": 0,
    "last_pending": 0,
    "total_scraped": 0,
}


def determine_batch_size(session: "AccountSession") -> int:
    current_pending = count_pending_scrape(session)
    s = _state

    s["wait_count"] += 1
    wait_no = s["wait_count"]

    delta = current_pending - s["last_pending"] if wait_no > 1 else 0
    s["last_pending"] = current_pending
    s["total_scraped"] += max(delta, 0)

    avg_per_cycle = s["total_scraped"] // wait_no if wait_no > 1 else delta
    batch_size = min(current_pending, max(1, avg_per_cycle))

    logger.debug(
        f"Throttle | Cycle {wait_no:04d} | "
        f"Pending {current_pending:4d} | "
        f"Scraped {delta:3d} | "
        f"Avg {avg_per_cycle:3d} | "
        f"Batch {batch_size:3d}"
    )

    return batch_size
