# linkedin/rate_limiter.py
from __future__ import annotations

import logging
from datetime import date

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, daily_limit: int | None = None, weekly_limit: int | None = None):
        self.daily_limit = daily_limit
        self.weekly_limit = weekly_limit
        self._daily_count = 0
        self._weekly_count = 0
        self._current_day = date.today()
        self._current_week = date.today().isocalendar()[1]
        self._daily_exhausted = False

    def _maybe_reset(self):
        today = date.today()
        week = today.isocalendar()[1]

        if today != self._current_day:
            self._daily_count = 0
            self._daily_exhausted = False
            self._current_day = today

        if week != self._current_week:
            self._weekly_count = 0
            self._current_week = week

    def can_execute(self) -> bool:
        self._maybe_reset()
        if self._daily_exhausted:
            return False
        if self.daily_limit is not None and self._daily_count >= self.daily_limit:
            return False
        if self.weekly_limit is not None and self._weekly_count >= self.weekly_limit:
            return False
        return True

    def record(self):
        self._maybe_reset()
        self._daily_count += 1
        self._weekly_count += 1

    def mark_daily_exhausted(self):
        self._daily_exhausted = True
        logger.warning("Rate limiter: daily limit externally exhausted")
