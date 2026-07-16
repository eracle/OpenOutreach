# tests/test_pools.py
"""Pool generators: qualify_source (drains the pool, discovers when dry),
ready_source (GP rank gate), and find_candidate (top of the chain). Mock
fetch_qualification_candidates, run_qualification, discover, find_ready_candidate,
and promote_to_ready at the pools import site."""
from contextlib import contextmanager
from itertools import islice
from unittest.mock import patch

from openoutreach.core.ml.qualifier import BayesianQualifier
from openoutreach.core.pipeline.pools import (
    find_candidate,
    qualify_source,
    ready_source,
)

PROFILE_URL = "https://www.linkedin.com/in/alice/"
CANDIDATE = {"lead_id": 1, "profile_url": PROFILE_URL, "meta": {}}
# A non-empty pool sentinel — nothing inspects a candidate's attributes here.
POOL = ["lead"]


@contextmanager
def _pool(candidates=POOL):
    """Patch fetch_qualification_candidates at the pools import site."""
    with patch("openoutreach.core.pipeline.pools.fetch_qualification_candidates",
               return_value=candidates):
        yield


class TestQualifySource:
    def test_yields_until_pool_dry_then_dry_discovery_ends(self):
        """Yields each run_qualification hit; a dry run + dry discovery ends it."""
        scorer = BayesianQualifier(seed=42)
        with (
            _pool(),
            patch("openoutreach.core.pipeline.pools.run_qualification",
                  side_effect=[PROFILE_URL, PROFILE_URL, None]),
            patch("openoutreach.core.pipeline.pools.discover", return_value=0) as mock_discover,
        ):
            results = list(qualify_source("session", scorer))

        assert results == [PROFILE_URL, PROFILE_URL]
        mock_discover.assert_called_once()  # one dry discover page ends the generator

    def test_backfills_via_discovery_then_qualifies(self):
        """When run_qualification comes up dry, a non-empty discovery page lets it retry."""
        scorer = BayesianQualifier(seed=42)
        with (
            _pool(),
            patch("openoutreach.core.pipeline.pools.run_qualification",
                  side_effect=[None, PROFILE_URL, None]),
            patch("openoutreach.core.pipeline.pools.discover",
                  side_effect=[5, 0]) as mock_discover,
        ):
            results = list(qualify_source("session", scorer))

        assert results == [PROFILE_URL]
        assert mock_discover.call_count == 2  # first backfills, second is dry → stop

    def test_empty_pool_pages_in_then_stops_when_dry(self):
        """An empty pool discovers a page; a dry page ends the generator."""
        scorer = BayesianQualifier(seed=42)
        with (
            _pool(candidates=[]),
            patch("openoutreach.core.pipeline.pools.run_qualification", return_value=None),
            patch("openoutreach.core.pipeline.pools.discover", return_value=0),
        ):
            assert list(qualify_source("session", scorer)) == []

    def test_discovers_only_when_the_pool_is_dry(self):
        """The pool drains before any widening — no "promising pool" gate.

        Two gates have been tried here and both were constants in disguise (see the
        pools module docstring). The contract is now flat: while there is anything
        to qualify, qualify it. Discovery is what happens when there is not.

        This is the regression that shipped in cae4e3b: a gate that always fired,
        widening after every single label, ran discovery 17x ahead of qualification.
        """
        scorer = BayesianQualifier(seed=42)
        with (
            _pool(),  # never dry
            patch("openoutreach.core.pipeline.pools.discover") as mock_discover,
            patch("openoutreach.core.pipeline.pools.run_qualification",
                  return_value=PROFILE_URL),
        ):
            results = list(islice(qualify_source("session", scorer), 5))

        assert results == [PROFILE_URL] * 5
        mock_discover.assert_not_called()


class TestReadySource:
    def test_yields_ready_candidates(self):
        scorer = BayesianQualifier(seed=42)
        with (
            _pool(),
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
            _pool(),
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
            _pool(),
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
            _pool(),
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
            _pool(),
            patch("openoutreach.core.pipeline.pools.find_ready_candidate", return_value=None),
            patch("openoutreach.core.pipeline.pools.promote_to_ready", return_value=0),
            patch("openoutreach.core.pipeline.pools.run_qualification", return_value=None),
            patch("openoutreach.core.pipeline.pools.discover", return_value=0),
        ):
            assert find_candidate("session", scorer) is None
