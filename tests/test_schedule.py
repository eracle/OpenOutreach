from __future__ import annotations

from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest

from linkedin.daemon import seconds_until_active


def _mock_now(year, month, day, hour, minute=0, tz="UTC"):
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(tz))


def _mock_config(*, enable=True, start=9, end=17, tz="UTC", rest_days=(5, 6)):
    """Return a Mock that walks like a SiteConfig row."""
    cfg = Mock()
    cfg.enable_active_hours = enable
    cfg.active_start_hour = start
    cfg.active_end_hour = end
    cfg.active_timezone = tz
    cfg.rest_days = list(rest_days)
    return cfg


@pytest.fixture(autouse=True)
def _default_schedule(settings):
    """Ensure tests use known schedule defaults."""


class TestSecondsUntilActive:
    def test_inside_active_window(self):
        cfg = _mock_config()
        with (
            patch("linkedin.models.SiteConfig.load", return_value=cfg),
            patch("linkedin.daemon.timezone.localtime", return_value=_mock_now(2026, 3, 18, 12)),
        ):
            assert seconds_until_active() == 0.0

    def test_before_start(self):
        cfg = _mock_config()
        with (
            patch("linkedin.models.SiteConfig.load", return_value=cfg),
            patch("linkedin.daemon.timezone.localtime", return_value=_mock_now(2026, 3, 18, 7)),
        ):
            result = seconds_until_active()
            assert result == pytest.approx(2 * 3600, abs=1)

    def test_after_end(self):
        cfg = _mock_config()
        with (
            patch("linkedin.models.SiteConfig.load", return_value=cfg),
            patch("linkedin.daemon.timezone.localtime", return_value=_mock_now(2026, 3, 18, 18)),
        ):
            result = seconds_until_active()
            assert result == pytest.approx(15 * 3600, abs=1)  # 15h to Thu 9am

    def test_friday_evening_skips_weekend(self):
        cfg = _mock_config()
        # Fri Mar 20 2026 is a Friday (weekday=4)
        with (
            patch("linkedin.models.SiteConfig.load", return_value=cfg),
            patch("linkedin.daemon.timezone.localtime", return_value=_mock_now(2026, 3, 20, 18)),
        ):
            result = seconds_until_active()
            # Next active: Mon Mar 23 9am = 63h away
            assert result == pytest.approx(63 * 3600, abs=1)

    def test_saturday_skips_to_monday(self):
        cfg = _mock_config()
        # Sat Mar 21 2026 noon
        with (
            patch("linkedin.models.SiteConfig.load", return_value=cfg),
            patch("linkedin.daemon.timezone.localtime", return_value=_mock_now(2026, 3, 21, 12)),
        ):
            result = seconds_until_active()
            # Next active: Mon Mar 23 9am = 45h away
            assert result == pytest.approx(45 * 3600, abs=1)

    def test_timezone_respected(self):
        cfg = _mock_config(tz="Europe/Berlin")
        # Wed 8am Berlin = still before 9am start
        with (
            patch("linkedin.models.SiteConfig.load", return_value=cfg),
            patch("linkedin.daemon.timezone.localtime", return_value=_mock_now(2026, 3, 18, 8, tz="Europe/Berlin")),
        ):
            result = seconds_until_active()
            assert result == pytest.approx(3600, abs=1)

    def test_no_rest_days(self):
        cfg = _mock_config(rest_days=())
        # Sat noon, but no rest days configured
        with (
            patch("linkedin.models.SiteConfig.load", return_value=cfg),
            patch("linkedin.daemon.timezone.localtime", return_value=_mock_now(2026, 3, 21, 12)),
        ):
            assert seconds_until_active() == 0.0

    def test_at_exact_start(self):
        cfg = _mock_config()
        with (
            patch("linkedin.models.SiteConfig.load", return_value=cfg),
            patch("linkedin.daemon.timezone.localtime", return_value=_mock_now(2026, 3, 18, 9)),
        ):
            assert seconds_until_active() == 0.0

    def test_at_exact_end(self):
        cfg = _mock_config()
        with (
            patch("linkedin.models.SiteConfig.load", return_value=cfg),
            patch("linkedin.daemon.timezone.localtime", return_value=_mock_now(2026, 3, 18, 17)),
        ):
            result = seconds_until_active()
            # Should be outside (end is exclusive), next day 9am = 16h
            assert result == pytest.approx(16 * 3600, abs=1)

    def test_disabled_always_active(self):
        cfg = _mock_config(enable=False)
        # Outside hours on a rest day — should still return 0 when disabled
        with (
            patch("linkedin.models.SiteConfig.load", return_value=cfg),
            patch("linkedin.daemon.timezone.localtime", return_value=_mock_now(2026, 3, 21, 23)),
        ):
            assert seconds_until_active() == 0.0
