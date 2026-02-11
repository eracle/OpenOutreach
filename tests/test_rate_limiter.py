# tests/test_rate_limiter.py
from datetime import date
from unittest.mock import patch

from linkedin.rate_limiter import RateLimiter


class TestRateLimiterDaily:
    def test_within_daily_limit(self):
        rl = RateLimiter(daily_limit=3)
        assert rl.can_execute()

    def test_at_daily_limit(self):
        rl = RateLimiter(daily_limit=2)
        rl.record()
        rl.record()
        assert not rl.can_execute()

    def test_above_daily_limit(self):
        rl = RateLimiter(daily_limit=1)
        rl.record()
        rl.record()
        assert not rl.can_execute()


class TestRateLimiterWeekly:
    def test_within_weekly_limit(self):
        rl = RateLimiter(weekly_limit=5)
        for _ in range(4):
            rl.record()
        assert rl.can_execute()

    def test_at_weekly_limit(self):
        rl = RateLimiter(weekly_limit=3)
        for _ in range(3):
            rl.record()
        assert not rl.can_execute()


class TestRateLimiterReset:
    def test_daily_reset(self):
        rl = RateLimiter(daily_limit=1)
        rl.record()
        assert not rl.can_execute()

        # Simulate next day
        rl._current_day = date(2020, 1, 1)
        assert rl.can_execute()
        assert rl._daily_count == 0

    def test_weekly_reset(self):
        rl = RateLimiter(weekly_limit=1)
        rl.record()
        assert not rl.can_execute()

        # Simulate next week
        rl._current_week = 0
        assert rl.can_execute()
        assert rl._weekly_count == 0


class TestRateLimiterExhausted:
    def test_mark_daily_exhausted(self):
        rl = RateLimiter(daily_limit=100)
        assert rl.can_execute()
        rl.mark_daily_exhausted()
        assert not rl.can_execute()

    def test_exhausted_resets_next_day(self):
        rl = RateLimiter(daily_limit=100)
        rl.mark_daily_exhausted()
        assert not rl.can_execute()

        # Simulate next day
        rl._current_day = date(2020, 1, 1)
        assert rl.can_execute()


class TestRateLimiterUnlimited:
    def test_no_limits(self):
        rl = RateLimiter()
        for _ in range(1000):
            rl.record()
        assert rl.can_execute()

    def test_daily_none_weekly_set(self):
        rl = RateLimiter(daily_limit=None, weekly_limit=2)
        rl.record()
        rl.record()
        assert not rl.can_execute()

    def test_daily_set_weekly_none(self):
        rl = RateLimiter(daily_limit=2, weekly_limit=None)
        rl.record()
        rl.record()
        assert not rl.can_execute()
