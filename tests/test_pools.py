# tests/test_pools.py
import pytest
from unittest.mock import patch, MagicMock, PropertyMock

import numpy as np

from linkedin.ml.qualifier import BayesianQualifier
from linkedin.pipeline.pools import (
    get_candidate,
    _positive_pool_empty,
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


def _make_candidate(lead_id, embedding_array, label=None):
    """Create a mock ProfileEmbedding candidate."""
    c = MagicMock()
    c.lead_id = lead_id
    c.embedding_array = embedding_array
    c.label = label
    return c


class TestPositivePoolEmpty:
    def test_empty_candidates(self):
        scorer = BayesianQualifier(seed=42)
        assert _positive_pool_empty(scorer, []) is False

    def test_explore_mode(self):
        """n_neg <= n_pos → explore mode → always False."""
        scorer = BayesianQualifier(seed=42)
        candidates = [_make_candidate(1, np.zeros(384, dtype=np.float32))]

        with patch.object(type(scorer), "class_counts", new_callable=PropertyMock, return_value=(2, 3)):
            assert _positive_pool_empty(scorer, candidates) is False

    def test_cold_start(self):
        """Unfitted qualifier (predict_probs=None) → False."""
        scorer = BayesianQualifier(seed=42)
        candidates = [_make_candidate(1, np.zeros(384, dtype=np.float32))]

        with (
            patch.object(type(scorer), "class_counts", new_callable=PropertyMock, return_value=(3, 2)),
            patch.object(scorer, "predict_probs", return_value=None),
        ):
            assert _positive_pool_empty(scorer, candidates) is False

    def test_exploit_no_high_prob(self):
        """Exploit mode with all P < 0.5 → True."""
        scorer = BayesianQualifier(seed=42)
        candidates = [_make_candidate(1, np.zeros(384, dtype=np.float32))]

        with (
            patch.object(type(scorer), "class_counts", new_callable=PropertyMock, return_value=(5, 2)),
            patch.object(scorer, "predict_probs", return_value=np.array([0.3])),
        ):
            assert _positive_pool_empty(scorer, candidates) is True

    def test_exploit_has_high_prob(self):
        """Exploit mode with some P > 0.5 → False."""
        scorer = BayesianQualifier(seed=42)
        candidates = [
            _make_candidate(1, np.zeros(384, dtype=np.float32)),
            _make_candidate(2, np.ones(384, dtype=np.float32)),
        ]

        with (
            patch.object(type(scorer), "class_counts", new_callable=PropertyMock, return_value=(5, 2)),
            patch.object(scorer, "predict_probs", return_value=np.array([0.3, 0.7])),
        ):
            assert _positive_pool_empty(scorer, candidates) is False


class TestSearchSource:
    def test_yields_keywords(self):
        with patch("linkedin.pipeline.pools.search_one", side_effect=["kw1", "kw2", None]):
            results = list(search_source("session"))
        assert results == ["kw1", "kw2"]

    def test_stops_on_none(self):
        with patch("linkedin.pipeline.pools.search_one", return_value=None):
            results = list(search_source("session"))
        assert results == []


class TestQualifySource:
    def test_qualifies_without_search_when_pool_ok(self):
        """When pool has candidates and no exploit gap, qualifies directly."""
        scorer = BayesianQualifier(seed=42)
        candidates = [_make_candidate(1, np.zeros(384, dtype=np.float32))]

        with (
            patch("linkedin.pipeline.pools.get_unlabeled_candidates", return_value=candidates),
            patch("linkedin.pipeline.pools._positive_pool_empty", return_value=False),
            patch("linkedin.pipeline.pools.qualify_one", side_effect=["alice", None]),
            patch("linkedin.pipeline.pools.search_one") as mock_search,
        ):
            results = list(qualify_source("session", scorer))

        assert results == ["alice"]
        mock_search.assert_not_called()

    def test_searches_once_when_pool_empty_exploit(self):
        """In exploit mode with no P > 0.5, searches once upfront then qualifies multiple."""
        scorer = BayesianQualifier(seed=42)
        candidates = [_make_candidate(1, np.zeros(384, dtype=np.float32))]

        with (
            patch("linkedin.pipeline.pools.get_unlabeled_candidates", return_value=candidates),
            patch("linkedin.pipeline.pools._positive_pool_empty", return_value=True),
            patch("linkedin.pipeline.pools.qualify_one", side_effect=["alice", "bob", None]),
            patch("linkedin.pipeline.pools.search_one", return_value="kw1") as mock_search,
        ):
            results = list(qualify_source("session", scorer))

        assert results == ["alice", "bob"]
        # Only one search — the upfront pool quality check
        assert mock_search.call_count == 1

    def test_searches_when_no_candidates(self):
        """When no candidates at all, searches to bring some in."""
        scorer = BayesianQualifier(seed=42)
        candidates = [_make_candidate(1, np.zeros(384, dtype=np.float32))]

        with (
            # First call: upfront pool check (empty → _positive_pool_empty=False).
            # Second call: loop iteration (empty → triggers search).
            # Third call: after search (has candidates now).
            # Fourth call: next loop iteration (has candidates).
            patch("linkedin.pipeline.pools.get_unlabeled_candidates",
                  side_effect=[[], [], candidates, candidates]),
            patch("linkedin.pipeline.pools._positive_pool_empty", return_value=False),
            patch("linkedin.pipeline.pools.qualify_one", side_effect=["alice", None]),
            patch("linkedin.pipeline.pools.search_one", return_value="kw1") as mock_search,
        ):
            results = list(qualify_source("session", scorer))

        assert results == ["alice"]
        assert mock_search.call_count == 1

    def test_stops_when_search_exhausted_and_no_candidates(self):
        """When no candidates and search returns None, generator stops."""
        scorer = BayesianQualifier(seed=42)

        with (
            patch("linkedin.pipeline.pools.get_unlabeled_candidates", return_value=[]),
            patch("linkedin.pipeline.pools.search_one", return_value=None),
            patch("linkedin.pipeline.pools.qualify_one") as mock_qualify,
        ):
            results = list(qualify_source("session", scorer))

        assert results == []
        mock_qualify.assert_not_called()

    def test_search_does_not_loop_without_qualifying(self):
        """Regression: pool quality search fires once upfront, not per qualify."""
        scorer = BayesianQualifier(seed=42)
        candidates = [_make_candidate(1, np.zeros(384, dtype=np.float32))]

        with (
            patch("linkedin.pipeline.pools.get_unlabeled_candidates", return_value=candidates),
            patch("linkedin.pipeline.pools._positive_pool_empty", return_value=True),
            patch("linkedin.pipeline.pools.qualify_one", side_effect=["alice", "bob", "carol", None]),
            patch("linkedin.pipeline.pools.search_one", return_value="kw") as mock_search,
        ):
            results = list(qualify_source("session", scorer))

        assert results == ["alice", "bob", "carol"]
        # Only ONE search (the upfront pool quality check), not one per qualify
        assert mock_search.call_count == 1


@pytest.mark.django_db
class TestGetCandidate:
    @pytest.fixture(autouse=True)
    def _db(self, embeddings_db):
        pass

    def test_backfills_then_returns(self, fake_session):
        scorer = BayesianQualifier(seed=42)
        candidate = {"public_identifier": "alice", "profile": SAMPLE_PROFILE}
        candidates = [_make_candidate(1, np.zeros(384, dtype=np.float32))]

        with (
            patch("linkedin.pipeline.pools.get_ready_candidate", side_effect=[None, candidate]),
            patch("linkedin.pipeline.pools.promote_to_ready", side_effect=[0, 1]),
            patch("linkedin.pipeline.pools.get_unlabeled_candidates", return_value=candidates),
            patch("linkedin.pipeline.pools._positive_pool_empty", return_value=False),
            patch("linkedin.pipeline.pools.qualify_one", return_value="alice"),
        ):
            assert get_candidate(fake_session, scorer) == candidate

    def test_exhausted_returns_none(self, fake_session):
        scorer = BayesianQualifier(seed=42)

        with (
            patch("linkedin.pipeline.pools.get_ready_candidate", return_value=None),
            patch("linkedin.pipeline.pools.promote_to_ready", return_value=0),
            patch("linkedin.pipeline.pools.get_unlabeled_candidates", return_value=[]),
            patch("linkedin.pipeline.pools.search_one", return_value=None),
        ):
            assert get_candidate(fake_session, scorer) is None

    def test_partner_skips_backfill(self, fake_session):
        scorer = BayesianQualifier(seed=42)

        with (
            patch("linkedin.pipeline.pools.get_qualified_profiles", return_value=[]),
            patch("linkedin.pipeline.pools.qualify_one") as mock_qualify,
        ):
            assert get_candidate(fake_session, scorer, is_partner=True) is None
            mock_qualify.assert_not_called()

    def test_promote_after_qualify(self, fake_session):
        """After qualify_one produces a label, promote_to_ready is retried."""
        scorer = BayesianQualifier(seed=42)
        candidate = {"public_identifier": "alice"}
        candidates = [_make_candidate(1, np.zeros(384, dtype=np.float32))]

        with (
            patch("linkedin.pipeline.pools.get_ready_candidate", side_effect=[None, candidate]),
            # First promote: 0 (triggers qualify). Second promote (after qualify): 1.
            # Third would be from get_ready_candidate succeeding.
            patch("linkedin.pipeline.pools.promote_to_ready", side_effect=[0, 1]),
            patch("linkedin.pipeline.pools.get_unlabeled_candidates", return_value=candidates),
            patch("linkedin.pipeline.pools._positive_pool_empty", return_value=False),
            patch("linkedin.pipeline.pools.qualify_one", return_value="alice"),
        ):
            assert get_candidate(fake_session, scorer) == candidate
