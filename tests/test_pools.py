# tests/test_pools.py
"""The qualify/discover engine: ``_advance`` (one explore-or-exploit unit of work)
and ``find_candidate`` (the loop that surfaces a ready lead). Mock
fetch_qualification_candidates, run_qualification, discover, find_ready_candidate,
and promote_to_ready at the pools import site.

The explore/exploit split is the qualifier's ``acquisition_mode`` (mocked directly);
the exploit gate reads ``qualifier.predict_probs`` via ``consumable_candidates``, so
the qualifier is mocked at that boundary too: a score below ``min_gp_confidence``
cannot reach email (widen), one at or above it is worth a converting qualification.
"""
from contextlib import contextmanager
from unittest.mock import Mock, patch

import numpy as np

from openoutreach.core.ml.qualifier import BayesianQualifier
from openoutreach.core.pipeline.pools import _advance, find_candidate

PROFILE_URL = "https://www.linkedin.com/in/alice/"
CANDIDATE = {"lead_id": 1, "profile_url": PROFILE_URL, "meta": {}}


def _qualifier(mode, probs=None):
    """A qualifier in ``mode`` ("exploit (p)" / "explore (BALD)" / None) scoring the
    pool at ``probs`` (None is an unfitted GP)."""
    qualifier = Mock()
    qualifier.acquisition_mode.return_value = mode
    qualifier.predict_probs.return_value = (
        None if probs is None else np.asarray(probs, dtype=float)
    )
    return qualifier


@contextmanager
def _engine(candidates, *, qualify=PROFILE_URL, discovered=0):
    """Patch the engine's collaborators; yield the (run_qualification, discover) mocks."""
    with (
        patch("openoutreach.core.pipeline.pools.fetch_qualification_candidates",
              return_value=candidates),
        patch("openoutreach.core.pipeline.pools.run_qualification",
              return_value=qualify) as mock_qualify,
        patch("openoutreach.core.pipeline.pools.discover",
              return_value=discovered) as mock_discover,
    ):
        yield mock_qualify, mock_discover


class TestAdvanceExploit:
    def test_converts_a_lead_that_clears_the_gate(self):
        """A lead above the gate can reach email — qualify only that subset, don't widen."""
        weak = Mock(embedding_array=np.zeros(384))
        strong = Mock(embedding_array=np.ones(384))
        with _engine([weak, strong]) as (mock_qualify, mock_discover):
            with patch.dict("openoutreach.core.pipeline.pools.CAMPAIGN_CONFIG",
                            {"min_gp_confidence": 0.9}):
                assert _advance("session", _qualifier("exploit (p)", probs=[0.3, 0.95])) is True

        assert mock_qualify.call_args.kwargs["candidates"] == [strong]
        mock_discover.assert_not_called()

    def test_discovers_when_nothing_clears_the_gate(self):
        """No lead can reach email — there's nothing worth qualifying, so widen instead."""
        lead = Mock(embedding_array=np.zeros(384))
        with _engine([lead], discovered=100) as (mock_qualify, mock_discover):
            with patch.dict("openoutreach.core.pipeline.pools.CAMPAIGN_CONFIG",
                            {"min_gp_confidence": 0.9}):
                assert _advance("session", _qualifier("exploit (p)", probs=[0.3])) is True

        mock_qualify.assert_not_called()
        mock_discover.assert_called_once()


class TestAdvanceExplore:
    def test_labels_the_whole_pool_with_no_gate(self):
        """Explore hands the LLM the full pool — BALD wants the uncertain lead the gate
        would strip out."""
        pool = [Mock(embedding_array=np.zeros(384)), Mock(embedding_array=np.ones(384))]
        with _engine(pool) as (mock_qualify, mock_discover):
            assert _advance("session", _qualifier("explore (BALD)")) is True

        assert mock_qualify.call_args.kwargs["candidates"] == pool
        mock_discover.assert_not_called()

    def test_cold_start_labels_without_a_gate(self):
        """An unfitted GP (acquisition_mode None) labels like explore — one label moves it."""
        pool = [Mock(embedding_array=np.zeros(384))]
        with _engine(pool) as (mock_qualify, mock_discover):
            assert _advance("session", _qualifier(None)) is True

        assert mock_qualify.call_args.kwargs["candidates"] == pool
        mock_discover.assert_not_called()

    def test_empty_pool_pages_in_then_labels(self):
        """No lead to label → discover a page, then label it."""
        with (
            patch("openoutreach.core.pipeline.pools.fetch_qualification_candidates",
                  side_effect=[[], [Mock(embedding_array=np.zeros(384))]]),
            patch("openoutreach.core.pipeline.pools.run_qualification", return_value=PROFILE_URL),
            patch("openoutreach.core.pipeline.pools.discover", return_value=100) as mock_discover,
        ):
            assert _advance("session", _qualifier("explore (BALD)")) is True
        mock_discover.assert_called_once()

    def test_empty_pool_and_dry_discovery_stalls(self):
        """No lead to label and nothing to discover → the engine has nothing to do."""
        with _engine([], qualify=None, discovered=0) as (_, mock_discover):
            assert _advance("session", _qualifier("explore (BALD)")) is False
        mock_discover.assert_called_once()


class TestFindCandidate:
    def test_returns_a_ready_candidate_immediately(self):
        scorer = BayesianQualifier(seed=42)
        with (
            patch("openoutreach.core.pipeline.pools.find_ready_candidate", return_value=CANDIDATE),
            patch("openoutreach.core.pipeline.pools.promote_to_ready") as mock_promote,
        ):
            assert find_candidate("session", scorer) == CANDIDATE
        mock_promote.assert_not_called()

    def test_promotes_then_returns(self):
        scorer = BayesianQualifier(seed=42)
        with (
            patch("openoutreach.core.pipeline.pools.find_ready_candidate",
                  side_effect=[None, CANDIDATE]),
            patch("openoutreach.core.pipeline.pools.promote_to_ready", return_value=1),
        ):
            assert find_candidate("session", scorer) == CANDIDATE

    def test_advances_then_promotes_then_returns(self):
        """No ready lead, nothing to promote yet → advance one unit, which qualifies a
        lead the next promote pass then lifts to ready."""
        scorer = BayesianQualifier(seed=42)
        with (
            patch("openoutreach.core.pipeline.pools.find_ready_candidate",
                  side_effect=[None, CANDIDATE]),
            patch("openoutreach.core.pipeline.pools.promote_to_ready", side_effect=[0, 1]),
            patch("openoutreach.core.pipeline.pools._advance", return_value=True) as mock_advance,
        ):
            assert find_candidate("session", scorer) == CANDIDATE
        mock_advance.assert_called_once()

    def test_stalled_engine_returns_none(self):
        scorer = BayesianQualifier(seed=42)
        with (
            patch("openoutreach.core.pipeline.pools.find_ready_candidate", return_value=None),
            patch("openoutreach.core.pipeline.pools.promote_to_ready", return_value=0),
            patch("openoutreach.core.pipeline.pools._advance", return_value=False),
        ):
            assert find_candidate("session", scorer) is None
