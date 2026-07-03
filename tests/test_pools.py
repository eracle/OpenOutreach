# tests/test_pools.py
"""Pool generators: qualify_source (qualify → discover backfill), ready_source
(GP rank gate), and find_candidate (top of the chain). Mock run_qualification,
discover, find_ready_candidate, and promote_to_ready at the pools import site."""
from unittest.mock import patch

import pytest

from openoutreach.core.ml.qualifier import BayesianQualifier
from openoutreach.core.pipeline.pools import (
    find_candidate,
    qualify_source,
    ready_source,
)

PROFILE_URL = "https://www.linkedin.com/in/alice/"
CANDIDATE = {"lead_id": 1, "profile_url": PROFILE_URL, "meta": {}}


class TestQualifySource:
    def test_yields_until_pool_dry_then_dry_discovery_ends(self):
        """Yields each run_qualification hit; a dry pool + dry discovery ends it."""
        scorer = BayesianQualifier(seed=42)
        with (
            patch("openoutreach.core.pipeline.pools.run_qualification",
                  side_effect=[PROFILE_URL, PROFILE_URL, None]),
            patch("openoutreach.core.pipeline.pools.discover", return_value=0) as mock_discover,
        ):
            results = list(qualify_source("session", scorer))

        assert results == [PROFILE_URL, PROFILE_URL]
        mock_discover.assert_called_once()  # one dry discover page ends the generator

    def test_backfills_via_discovery_then_qualifies(self):
        """When the pool goes dry, a non-empty discovery page lets qualify retry."""
        scorer = BayesianQualifier(seed=42)
        with (
            patch("openoutreach.core.pipeline.pools.run_qualification",
                  side_effect=[None, PROFILE_URL, None]),
            patch("openoutreach.core.pipeline.pools.discover",
                  side_effect=[5, 0]) as mock_discover,
        ):
            results = list(qualify_source("session", scorer))

        assert results == [PROFILE_URL]
        assert mock_discover.call_count == 2  # first backfills, second is dry → stop

    def test_empty_pool_and_no_discovery_stops_immediately(self):
        scorer = BayesianQualifier(seed=42)
        with (
            patch("openoutreach.core.pipeline.pools.run_qualification", return_value=None),
            patch("openoutreach.core.pipeline.pools.discover", return_value=0),
        ):
            assert list(qualify_source("session", scorer)) == []


class TestReadySource:
    def test_yields_ready_candidates(self):
        scorer = BayesianQualifier(seed=42)
        with (
            patch("openoutreach.core.pipeline.pools.find_ready_candidate",
                  side_effect=[CANDIDATE, None]),
            patch("openoutreach.core.pipeline.pools.promote_to_ready", return_value=0),
            patch("openoutreach.core.pipeline.pools.run_qualification", return_value=None),
            patch("openoutreach.core.pipeline.pools.discover", return_value=0),
        ):
            gen = ready_source("session", scorer, threshold=0.5)
            assert next(gen) == CANDIDATE

    def test_promotes_from_qualified_when_ready_pool_empty(self):
        scorer = BayesianQualifier(seed=42)
        with (
            patch("openoutreach.core.pipeline.pools.find_ready_candidate",
                  side_effect=[None, CANDIDATE]),
            patch("openoutreach.core.pipeline.pools.promote_to_ready",
                  side_effect=[1, 0]),
            patch("openoutreach.core.pipeline.pools.run_qualification", return_value=None),
            patch("openoutreach.core.pipeline.pools.discover", return_value=0),
        ):
            gen = ready_source("session", scorer, threshold=0.5)
            assert next(gen) == CANDIDATE

    def test_exhausts_when_all_upstream_dry(self):
        scorer = BayesianQualifier(seed=42)
        with (
            patch("openoutreach.core.pipeline.pools.find_ready_candidate", return_value=None),
            patch("openoutreach.core.pipeline.pools.promote_to_ready", return_value=0),
            patch("openoutreach.core.pipeline.pools.run_qualification", return_value=None),
            patch("openoutreach.core.pipeline.pools.discover", return_value=0),
        ):
            assert list(ready_source("session", scorer, threshold=0.5)) == []


class TestFindCandidate:
    def test_backfills_then_returns(self):
        scorer = BayesianQualifier(seed=42)
        with (
            patch("openoutreach.core.pipeline.pools.find_ready_candidate",
                  side_effect=[None, CANDIDATE]),
            patch("openoutreach.core.pipeline.pools.promote_to_ready", side_effect=[0, 1]),
            patch("openoutreach.core.pipeline.pools.run_qualification", return_value=PROFILE_URL),
            patch("openoutreach.core.pipeline.pools.discover", return_value=0),
        ):
            assert find_candidate("session", scorer) == CANDIDATE

    def test_exhausted_returns_none(self):
        scorer = BayesianQualifier(seed=42)
        with (
            patch("openoutreach.core.pipeline.pools.find_ready_candidate", return_value=None),
            patch("openoutreach.core.pipeline.pools.promote_to_ready", return_value=0),
            patch("openoutreach.core.pipeline.pools.run_qualification", return_value=None),
            patch("openoutreach.core.pipeline.pools.discover", return_value=0),
        ):
            assert find_candidate("session", scorer) is None
