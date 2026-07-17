# tests/test_pools.py
"""Pool generators: qualify_source (consume / cold-start states), ready_source
(GP rank gate), and find_candidate (top of the chain). Mock
fetch_qualification_candidates, run_qualification, discover, find_ready_candidate,
and promote_to_ready at the pools import site.

The state switch reads nothing but ``qualifier.predict_probs``, so the qualifier is
mocked at that boundary: ``None`` is an unfitted GP (cold start by definition), a
score below ``min_gp_confidence`` is a pool that cannot reach email, and one at or
above it is consume.
"""
from contextlib import contextmanager
from itertools import islice
from unittest.mock import Mock, patch

import numpy as np
import pytest

from openoutreach.core.ml.qualifier import BayesianQualifier
from openoutreach.core.pipeline.pools import (
    find_candidate,
    qualify_source,
    ready_source,
)
from tests.factories import LeadFactory

PROFILE_URL = "https://www.linkedin.com/in/alice/"
CANDIDATE = {"lead_id": 1, "profile_url": PROFILE_URL, "meta": {}}
THRESHOLD = 0.9


@pytest.fixture
def pool():
    """A one-lead pool — the state switch reads only its embedding."""
    return [LeadFactory(embedded=True)]


def _qualifier(probs):
    """A qualifier scoring the pool at ``probs``; ``None`` is an unfitted GP."""
    qualifier = Mock()
    qualifier.predict_probs.return_value = (
        None if probs is None else np.asarray(probs, dtype=float)
    )
    return qualifier


@contextmanager
def _pool(candidates):
    """Patch fetch_qualification_candidates at the pools import site."""
    with patch("openoutreach.core.pipeline.pools.fetch_qualification_candidates",
               return_value=candidates):
        yield


class TestQualifySource:
    def test_consume_qualifies_without_discovering(self, pool):
        """A lead above the threshold can reach email — spend the LLM call, don't widen."""
        with (
            _pool(pool),
            patch("openoutreach.core.pipeline.pools.run_qualification",
                  return_value=PROFILE_URL) as mock_qualify,
            patch("openoutreach.core.pipeline.pools.discover") as mock_discover,
        ):
            results = list(islice(qualify_source("session", _qualifier([0.95]), THRESHOLD), 5))

        assert results == [PROFILE_URL] * 5
        assert mock_qualify.call_args.kwargs["candidates"] == pool
        mock_discover.assert_not_called()

    def test_consume_offers_the_llm_only_the_leads_that_can_reach_email(self):
        """A mixed pool hands over the strong subset — never the whole pool.

        The weak lead would be qualified and then parked by promote_to_ready, so the
        call would buy a label and nothing else. That is the cold-start trade, and
        this is not the cold start.
        """
        weak, strong = LeadFactory(embedded=True), LeadFactory(embedded=True)
        with (
            _pool([weak, strong]),
            patch("openoutreach.core.pipeline.pools.run_qualification",
                  return_value=PROFILE_URL) as mock_qualify,
            patch("openoutreach.core.pipeline.pools.discover") as mock_discover,
        ):
            list(islice(qualify_source("session", _qualifier([0.3, 0.95]), THRESHOLD), 1))

        assert mock_qualify.call_args.kwargs["candidates"] == [strong]
        mock_discover.assert_not_called()

    def test_cold_start_discovers_a_page_per_label(self, pool):
        """Nothing can reach email — widen, then spend exactly one label. The 1:100.

        The one lead is chosen with no threshold: requiring one would be circular,
        since nothing meeting it is what put us here.
        """
        with (
            _pool(pool),
            patch("openoutreach.core.pipeline.pools.run_qualification",
                  return_value=PROFILE_URL) as mock_qualify,
            patch("openoutreach.core.pipeline.pools.discover", return_value=100) as mock_discover,
        ):
            results = list(islice(qualify_source("session", _qualifier([0.3]), THRESHOLD), 3))

        assert results == [PROFILE_URL] * 3
        assert mock_discover.call_count == 3
        assert mock_qualify.call_args.kwargs.get("candidates") is None

    def test_unfitted_gp_is_cold_start(self, pool):
        """predict_probs → None: a GP that can say nothing cannot say a lead is strong."""
        with (
            _pool(pool),
            patch("openoutreach.core.pipeline.pools.run_qualification",
                  return_value=PROFILE_URL),
            patch("openoutreach.core.pipeline.pools.discover", return_value=100) as mock_discover,
        ):
            list(islice(qualify_source("session", _qualifier(None), THRESHOLD), 1))

        mock_discover.assert_called_once()

    def test_empty_pool_pages_in_then_stops_when_dry(self):
        """An empty pool discovers a page; a dry page ends the generator."""
        with (
            _pool([]),
            patch("openoutreach.core.pipeline.pools.run_qualification", return_value=None),
            patch("openoutreach.core.pipeline.pools.discover", return_value=0),
        ):
            assert list(qualify_source("session", _qualifier(None), THRESHOLD)) == []

    def test_ends_when_it_can_neither_qualify_nor_discover(self, pool):
        """The only exit: nothing left to label and nothing left to find."""
        with (
            _pool(pool),
            patch("openoutreach.core.pipeline.pools.run_qualification", return_value=None),
            patch("openoutreach.core.pipeline.pools.discover", return_value=0),
        ):
            assert list(qualify_source("session", _qualifier([0.3]), THRESHOLD)) == []


class TestReadySource:
    def test_yields_ready_candidates(self, pool):
        scorer = BayesianQualifier(seed=42)
        with (
            _pool(pool),
            patch("openoutreach.core.pipeline.pools.find_ready_candidate",
                  side_effect=[CANDIDATE, None]),
            patch("openoutreach.core.pipeline.pools.promote_to_ready", return_value=0),
            patch("openoutreach.core.pipeline.pools.run_qualification", return_value=None),
            patch("openoutreach.core.pipeline.pools.discover", return_value=0),
        ):
            gen = ready_source("session", scorer, threshold=0.5)
            assert next(gen) == CANDIDATE

    def test_promotes_from_qualified_when_ready_pool_empty(self, pool):
        scorer = BayesianQualifier(seed=42)
        with (
            _pool(pool),
            patch("openoutreach.core.pipeline.pools.find_ready_candidate",
                  side_effect=[None, CANDIDATE]),
            patch("openoutreach.core.pipeline.pools.promote_to_ready",
                  side_effect=[1, 0]),
            patch("openoutreach.core.pipeline.pools.run_qualification", return_value=None),
            patch("openoutreach.core.pipeline.pools.discover", return_value=0),
        ):
            gen = ready_source("session", scorer, threshold=0.5)
            assert next(gen) == CANDIDATE

    def test_exhausts_when_all_upstream_dry(self, pool):
        scorer = BayesianQualifier(seed=42)
        with (
            _pool(pool),
            patch("openoutreach.core.pipeline.pools.find_ready_candidate", return_value=None),
            patch("openoutreach.core.pipeline.pools.promote_to_ready", return_value=0),
            patch("openoutreach.core.pipeline.pools.run_qualification", return_value=None),
            patch("openoutreach.core.pipeline.pools.discover", return_value=0),
        ):
            assert list(ready_source("session", scorer, threshold=0.5)) == []


class TestFindCandidate:
    def test_backfills_then_returns(self, pool):
        scorer = BayesianQualifier(seed=42)
        with (
            _pool(pool),
            patch("openoutreach.core.pipeline.pools.find_ready_candidate",
                  side_effect=[None, CANDIDATE]),
            patch("openoutreach.core.pipeline.pools.promote_to_ready", side_effect=[0, 1]),
            patch("openoutreach.core.pipeline.pools.run_qualification", return_value=PROFILE_URL),
            patch("openoutreach.core.pipeline.pools.discover", return_value=0),
        ):
            assert find_candidate("session", scorer) == CANDIDATE

    def test_exhausted_returns_none(self, pool):
        scorer = BayesianQualifier(seed=42)
        with (
            _pool(pool),
            patch("openoutreach.core.pipeline.pools.find_ready_candidate", return_value=None),
            patch("openoutreach.core.pipeline.pools.promote_to_ready", return_value=0),
            patch("openoutreach.core.pipeline.pools.run_qualification", return_value=None),
            patch("openoutreach.core.pipeline.pools.discover", return_value=0),
        ):
            assert find_candidate("session", scorer) is None
