# tests/test_pools.py
import pytest
from unittest.mock import patch, MagicMock, PropertyMock

import numpy as np

from linkedin.ml.qualifier import BayesianQualifier
from linkedin.pipeline.pools import (
    find_candidate,
    _needs_search,
    search_source,
    qualify_source,
    ready_source,
)


SAMPLE_PROFILE = {
    "first_name": "Alice",
    "last_name": "Smith",
    "headline": "Engineer",
    "positions": [{"company_name": "Acme"}],
}


def _make_candidate(lead_id, embedding_array):
    """Create a mock Lead candidate with embedding."""
    c = MagicMock()
    c.lead_id = lead_id
    c.embedding_array = embedding_array
    return c


class TestPositivePoolEmpty:
    def test_empty_candidates(self):
        scorer = BayesianQualifier(seed=42)
        assert _needs_search(scorer, []) is False

    def test_explore_mode(self):
        """n_neg <= n_pos → explore mode → always False."""
        scorer = BayesianQualifier(seed=42)
        candidates = [_make_candidate(1, np.zeros(384, dtype=np.float32))]

        with patch.object(type(scorer), "class_counts", new_callable=PropertyMock, return_value=(2, 3)):
            assert _needs_search(scorer, candidates) is False

    def test_cold_start(self):
        """Unfitted qualifier (predict_probs=None) → False."""
        scorer = BayesianQualifier(seed=42)
        candidates = [_make_candidate(1, np.zeros(384, dtype=np.float32))]

        with (
            patch.object(type(scorer), "class_counts", new_callable=PropertyMock, return_value=(3, 2)),
            patch.object(scorer, "predict_probs", return_value=None),
        ):
            assert _needs_search(scorer, candidates) is False

    def test_exploit_low_n_obs_adaptive_threshold(self):
        """Exploit mode with few obs → threshold=0 (adaptive) → False (qualify first)."""
        scorer = BayesianQualifier(seed=42)
        candidates = [
            _make_candidate(1, np.zeros(384, dtype=np.float32)),
            _make_candidate(2, np.ones(384, dtype=np.float32)),
        ]

        with (
            patch.object(type(scorer), "class_counts", new_callable=PropertyMock, return_value=(5, 2)),
            patch.object(type(scorer), "n_obs", new_callable=PropertyMock, return_value=7),
            patch.object(scorer, "predict_probs", return_value=np.array([0.1, 0.2])),
        ):
            # n_obs=7, 1/√7≈0.378 > base=0.25, threshold=0 → any P≥0 → not empty
            assert _needs_search(scorer, candidates) is False

    def test_exploit_high_n_obs_triggers_search(self):
        """Exploit mode with many obs → threshold rises → True (search)."""
        scorer = BayesianQualifier(seed=42)
        candidates = [
            _make_candidate(1, np.zeros(384, dtype=np.float32)),
            _make_candidate(2, np.ones(384, dtype=np.float32)),
        ]

        with (
            patch.object(type(scorer), "class_counts", new_callable=PropertyMock, return_value=(50, 20)),
            patch.object(type(scorer), "n_obs", new_callable=PropertyMock, return_value=100),
            patch.object(scorer, "predict_probs", return_value=np.array([0.05, 0.08])),
        ):
            # n_obs=100, 1/√100=0.1, threshold=max(0, 0.25-0.1)=0.15
            # max_p=0.08 < 0.15 → pool empty → True
            assert _needs_search(scorer, candidates) is True

    def test_exploit_degenerate_predictions(self):
        """Exploit mode with degenerate GP (all identical P) → False (skip search)."""
        scorer = BayesianQualifier(seed=42)
        candidates = [
            _make_candidate(1, np.zeros(384, dtype=np.float32)),
            _make_candidate(2, np.ones(384, dtype=np.float32)),
        ]

        with (
            patch.object(type(scorer), "class_counts", new_callable=PropertyMock, return_value=(5, 2)),
            patch.object(scorer, "predict_probs", return_value=np.array([0.15, 0.15])),
        ):
            assert _needs_search(scorer, candidates) is False

    def test_exploit_has_high_prob(self):
        """Exploit mode with some P above threshold → False."""
        scorer = BayesianQualifier(seed=42)
        candidates = [
            _make_candidate(1, np.zeros(384, dtype=np.float32)),
            _make_candidate(2, np.ones(384, dtype=np.float32)),
        ]

        with (
            patch.object(type(scorer), "class_counts", new_callable=PropertyMock, return_value=(5, 2)),
            patch.object(scorer, "predict_probs", return_value=np.array([0.1, 0.7])),
        ):
            assert _needs_search(scorer, candidates) is False


class TestSearchSource:
    def test_yields_keywords(self):
        with patch("linkedin.pipeline.pools.run_search", side_effect=["kw1", "kw2", None]):
            results = list(search_source("session"))
        assert results == ["kw1", "kw2"]

    def test_stops_on_none(self):
        with patch("linkedin.pipeline.pools.run_search", return_value=None):
            results = list(search_source("session"))
        assert results == []


class TestQualifySource:
    def test_qualifies_without_search_when_pool_ok(self):
        """When pool has candidates and _needs_search=False, qualifies directly."""
        scorer = BayesianQualifier(seed=42)
        candidates = [_make_candidate(1, np.zeros(384, dtype=np.float32))]

        with (
            patch("linkedin.pipeline.pools.fetch_qualification_candidates", return_value=candidates),
            patch("linkedin.pipeline.pools._needs_search", return_value=False),
            patch("linkedin.pipeline.pools.run_qualification", side_effect=["alice", None]),
            patch("linkedin.pipeline.pools.run_search") as mock_search,
        ):
            results = list(qualify_source("session", scorer))

        assert results == ["alice"]
        mock_search.assert_not_called()

    def test_searches_until_exhausted_when_pool_empty_exploit(self):
        """In exploit mode with empty positive pool, searches until search exhausts then qualifies."""
        scorer = BayesianQualifier(seed=42)
        candidates = [_make_candidate(1, np.zeros(384, dtype=np.float32))]

        with (
            patch("linkedin.pipeline.pools.fetch_qualification_candidates", return_value=candidates),
            # Always empty — search exhausts (returns None) and loop breaks
            patch("linkedin.pipeline.pools._needs_search", return_value=True),
            patch("linkedin.pipeline.pools.run_qualification", side_effect=["alice", None]),
            patch("linkedin.pipeline.pools.run_search", side_effect=["kw1", "kw2", None]) as mock_search,
        ):
            results = list(qualify_source("session", scorer))

        assert results == ["alice"]
        assert mock_search.call_count == 3  # kw1, kw2, None (exhausted)

    def test_search_stops_when_pool_fills(self):
        """In exploit mode, searching stops when _needs_search flips to False."""
        scorer = BayesianQualifier(seed=42)
        candidates = [_make_candidate(1, np.zeros(384, dtype=np.float32))]

        call_count = [0]

        def pool_empty_side_effect(q, c):
            call_count[0] += 1
            # First call: empty. Second call (after one search): not empty.
            return call_count[0] <= 1

        with (
            patch("linkedin.pipeline.pools.fetch_qualification_candidates", return_value=candidates),
            patch("linkedin.pipeline.pools._needs_search", side_effect=pool_empty_side_effect),
            patch("linkedin.pipeline.pools.run_qualification", side_effect=["alice", None]),
            patch("linkedin.pipeline.pools.run_search", return_value="kw") as mock_search,
        ):
            results = list(qualify_source("session", scorer))

        assert results == ["alice"]
        assert mock_search.call_count == 1

    def test_searches_when_no_candidates(self):
        """When no candidates at all, searches to bring some in."""
        scorer = BayesianQualifier(seed=42)
        candidates = [_make_candidate(1, np.zeros(384, dtype=np.float32))]

        with (
            patch("linkedin.pipeline.pools.fetch_qualification_candidates",
                  side_effect=[[], candidates, candidates]),
            patch("linkedin.pipeline.pools._needs_search", return_value=False),
            patch("linkedin.pipeline.pools.run_qualification", side_effect=["alice", None]),
            patch("linkedin.pipeline.pools.run_search", return_value="kw1") as mock_search,
        ):
            results = list(qualify_source("session", scorer))

        assert results == ["alice"]
        assert mock_search.call_count == 1

    def test_stops_when_search_exhausted_and_no_candidates(self):
        """When no candidates and search returns None, generator stops."""
        scorer = BayesianQualifier(seed=42)

        with (
            patch("linkedin.pipeline.pools.fetch_qualification_candidates", return_value=[]),
            patch("linkedin.pipeline.pools.run_search", return_value=None),
            patch("linkedin.pipeline.pools.run_qualification") as mock_qualify,
        ):
            results = list(qualify_source("session", scorer))

        assert results == []
        mock_qualify.assert_not_called()


@pytest.mark.django_db
class TestGetCandidate:
    @pytest.fixture(autouse=True)
    def _db(self, db):
        pass

    def test_backfills_then_returns(self, fake_session):
        scorer = BayesianQualifier(seed=42)
        candidate = {"public_identifier": "alice", "profile": SAMPLE_PROFILE}
        candidates = [_make_candidate(1, np.zeros(384, dtype=np.float32))]

        with (
            patch("linkedin.pipeline.pools.find_ready_candidate", side_effect=[None, candidate]),
            patch("linkedin.pipeline.pools.promote_to_ready", side_effect=[0, 1]),
            patch("linkedin.pipeline.pools.fetch_qualification_candidates", return_value=candidates),
            patch("linkedin.pipeline.pools._needs_search", return_value=False),
            patch("linkedin.pipeline.pools.run_qualification", return_value="alice"),
        ):
            assert find_candidate(fake_session, scorer) == candidate

    def test_exhausted_returns_none(self, fake_session):
        scorer = BayesianQualifier(seed=42)

        with (
            patch("linkedin.pipeline.pools.find_ready_candidate", return_value=None),
            patch("linkedin.pipeline.pools.promote_to_ready", return_value=0),
            patch("linkedin.pipeline.pools.fetch_qualification_candidates", return_value=[]),
            patch("linkedin.pipeline.pools.run_search", return_value=None),
        ):
            assert find_candidate(fake_session, scorer) is None

    def test_promote_after_qualify(self, fake_session):
        """After run_qualification produces a label, promote_to_ready is retried."""
        scorer = BayesianQualifier(seed=42)
        candidate = {"public_identifier": "alice"}
        candidates = [_make_candidate(1, np.zeros(384, dtype=np.float32))]

        with (
            patch("linkedin.pipeline.pools.find_ready_candidate", side_effect=[None, candidate]),
            patch("linkedin.pipeline.pools.promote_to_ready", side_effect=[0, 1]),
            patch("linkedin.pipeline.pools.fetch_qualification_candidates", return_value=candidates),
            patch("linkedin.pipeline.pools._needs_search", return_value=False),
            patch("linkedin.pipeline.pools.run_qualification", return_value="alice"),
        ):
            assert find_candidate(fake_session, scorer) == candidate
