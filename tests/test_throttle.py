# tests/test_throttle.py
from unittest.mock import patch

from linkedin.navigation.throttle import ThrottleState, INITIAL_BATCH


class TestThrottleState:
    def _make_throttle(self):
        return ThrottleState()

    @patch("linkedin.navigation.throttle.count_pending_scrape")
    def test_first_call_returns_initial_batch(self, mock_count):
        mock_count.return_value = 20
        ts = self._make_throttle()
        assert ts.determine_batch_size(None) == INITIAL_BATCH

    @patch("linkedin.navigation.throttle.count_pending_scrape")
    def test_adapts_to_processing_rate(self, mock_count):
        ts = self._make_throttle()

        # First call: 20 pending → returns INITIAL_BATCH
        mock_count.return_value = 20
        ts.determine_batch_size(None)

        # Second call: 15 pending → processed 5
        mock_count.return_value = 15
        batch = ts.determine_batch_size(None)
        assert batch == 5  # avg = 5/1

    @patch("linkedin.navigation.throttle.count_pending_scrape")
    def test_never_returns_zero(self, mock_count):
        ts = self._make_throttle()

        mock_count.return_value = 10
        ts.determine_batch_size(None)

        # No change in pending → 0 processed, but batch should be ≥ 1
        mock_count.return_value = 10
        batch = ts.determine_batch_size(None)
        assert batch >= 1

    @patch("linkedin.navigation.throttle.count_pending_scrape")
    def test_caps_at_pending_count(self, mock_count):
        ts = self._make_throttle()

        mock_count.return_value = 100
        ts.determine_batch_size(None)

        # Processed 90, but only 2 left
        mock_count.return_value = 2
        batch = ts.determine_batch_size(None)
        assert batch <= 2
