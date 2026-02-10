# linkedin/navigation/throttle.py

import logging

from linkedin.db.crm_profiles import count_pending_scrape

logger = logging.getLogger(__name__)

INITIAL_BATCH = 5  # ← the only number you ever touch


class ThrottleState:
    def __init__(self):
        self.last_pending = None
        self.total_processed = 0
        self.processed_cycles = 0

    def determine_batch_size(self, session) -> int:
        current = count_pending_scrape(session)

        if self.last_pending is None:  # first call ever
            self.last_pending = current
            return INITIAL_BATCH

        processed = max(self.last_pending - current, 0)

        if processed > 0:
            self.total_processed += processed
            self.processed_cycles += 1

        self.last_pending = current

        if self.processed_cycles == 0:
            batch = INITIAL_BATCH
        else:
            batch = self.total_processed // self.processed_cycles

        batch = min(batch, current or 0)  # don’t take more than exist
        batch = max(1, batch)  # never zero

        logger.debug(
            "Throttle | Pending=%2d | Processed=%2d | Avg=%d | Batch=%d",
            current, processed,
            self.total_processed // self.processed_cycles if self.processed_cycles else 0,
            batch
        )

        return batch


_throttle_state = ThrottleState()
determine_batch_size = _throttle_state.determine_batch_size
