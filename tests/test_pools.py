# tests/test_pools.py
"""Pool generators: qualify_source (discovery interleaved with qualify),
ready_source (GP rank gate), and find_candidate (top of the chain). Mock
fetch_qualification_candidates, run_qualification, discover, find_ready_candidate,
and promote_to_ready at the pools import site."""
from contextlib import contextmanager
from itertools import islice
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from openoutreach.core.ml.qualifier import BayesianQualifier
from openoutreach.core.pipeline.pools import (
    _needs_more_discovery,
    find_candidate,
    qualify_source,
    ready_source,
)

PROFILE_URL = "https://www.linkedin.com/in/alice/"
CANDIDATE = {"lead_id": 1, "profile_url": PROFILE_URL, "meta": {}}
# A non-empty pool sentinel; _needs_more_discovery short-circuits (cold
# start/explore) before ever touching a candidate's attributes.
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

    def test_qualifies_one_then_rechecks_before_widening(self):
        """Nothing promising → qualify one, re-check the moved GP, then widen once."""
        scorer = BayesianQualifier(seed=42)
        with (
            _pool(),
            # pre-check, re-check (still barren), next cycle
            patch("openoutreach.core.pipeline.pools._needs_more_discovery",
                  side_effect=[True, True, False]),
            patch("openoutreach.core.pipeline.pools.discover",
                  side_effect=[5, 0]) as mock_discover,
            patch("openoutreach.core.pipeline.pools.run_qualification",
                  side_effect=[PROFILE_URL, None]),
        ):
            results = list(qualify_source("session", scorer))

        assert results == [PROFILE_URL]
        # first discover is the widen the re-check earned, second is the dry
        # end-of-stream page after run_qualification returns None.
        assert mock_discover.call_count == 2

    def test_recheck_can_overturn_the_verdict_and_spare_the_move(self):
        """The label moved the GP and the pool now looks promising → no move spent.

        This is what the re-check buys: the pre-check's verdict was made by a
        model that hadn't seen the label yet.
        """
        scorer = BayesianQualifier(seed=42)
        with (
            _pool(),
            patch("openoutreach.core.pipeline.pools._needs_more_discovery",
                  side_effect=[True, False, False]),
            patch("openoutreach.core.pipeline.pools.discover",
                  return_value=0) as mock_discover,
            patch("openoutreach.core.pipeline.pools.run_qualification",
                  side_effect=[PROFILE_URL, None]),
        ):
            results = list(qualify_source("session", scorer))

        assert results == [PROFILE_URL]
        mock_discover.assert_called_once()  # only the end-of-stream dry page

    def test_one_frontier_move_per_label_never_a_burst(self):
        """A pool that stays barren earns one move per label, not a discover loop."""
        scorer = BayesianQualifier(seed=42)
        with (
            _pool(),
            patch("openoutreach.core.pipeline.pools._needs_more_discovery",
                  return_value=True),
            patch("openoutreach.core.pipeline.pools.discover",
                  return_value=100) as mock_discover,
            patch("openoutreach.core.pipeline.pools.run_qualification",
                  return_value=PROFILE_URL),
        ):
            results = list(islice(qualify_source("session", scorer), 3))

        assert results == [PROFILE_URL] * 3
        assert mock_discover.call_count == 3


def _fake_qualifier(class_counts, probs, n_obs=100, floor=0.75):
    """A qualifier stubbed at the boundary _needs_more_discovery reads from.

    ``floor`` is the GP's score at the configured percentile of its proven
    positives — the self-calibrating bar the pool is held to. None models a
    campaign with no positive yet.
    """
    q = MagicMock(spec=BayesianQualifier)
    q.class_counts = class_counts
    q.n_obs = n_obs
    q.predict_probs.return_value = None if probs is None else np.array(probs, dtype=float)
    q.positive_score_floor.return_value = floor
    return q


def _cands(n=2):
    return [MagicMock(embedding_array=np.zeros(4, dtype=np.float32)) for _ in range(n)]


class TestNeedsMoreDiscovery:
    def test_false_on_empty_candidates(self):
        assert _needs_more_discovery(BayesianQualifier(seed=42), []) is False

    def test_false_on_cold_start(self):
        """A fresh (unfitted, explore-mode) qualifier never forces discovery."""
        assert _needs_more_discovery(BayesianQualifier(seed=42), POOL) is False

    def test_true_when_pool_scores_below_the_proven_positives(self):
        """Nothing scoring like a lead that actually qualified → widen.

        These probs are well clear of any absolute floor — the regression this
        guards is a pool of respectable-looking leads that the GP nonetheless
        rates far below everything that has ever converted.
        """
        q = _fake_qualifier(class_counts=(10, 3), probs=[0.20, 0.25], floor=0.75)
        assert _needs_more_discovery(q, _cands()) is True

    def test_false_when_a_candidate_matches_a_proven_positive(self):
        q = _fake_qualifier(class_counts=(10, 3), probs=[0.20, 0.80], floor=0.75)
        assert _needs_more_discovery(q, _cands()) is False

    def test_false_when_no_positive_proven_yet(self):
        """No positive → no bar to hold the pool to → qualify to find the first."""
        q = _fake_qualifier(class_counts=(10, 0), probs=[0.01, 0.02], floor=None)
        assert _needs_more_discovery(q, _cands()) is False

    def test_bar_tracks_the_model_not_a_constant(self):
        """The same pool is barren against strong positives, fine against weak ones."""
        pool_probs = [0.30, 0.40]
        assert _needs_more_discovery(
            _fake_qualifier((10, 3), pool_probs, floor=0.75), _cands()) is True
        assert _needs_more_discovery(
            _fake_qualifier((10, 3), pool_probs, floor=0.35), _cands()) is False

    def test_false_in_explore_mode_even_with_low_probs(self):
        """More positives than negatives → explore; never force discovery."""
        q = _fake_qualifier(class_counts=(3, 10), probs=[0.01, 0.02], n_obs=100)
        assert _needs_more_discovery(q, _cands()) is False

    def test_false_when_gp_predictions_degenerate(self):
        """Identical predictions → discovering won't help; qualify from the pool."""
        q = _fake_qualifier(class_counts=(10, 3), probs=[0.05, 0.05], n_obs=100)
        assert _needs_more_discovery(q, _cands()) is False

    def test_false_when_probs_unavailable(self):
        q = _fake_qualifier(class_counts=(10, 3), probs=None, n_obs=100)
        assert _needs_more_discovery(q, _cands()) is False

    def test_false_early_when_the_model_has_barely_learned(self):
        """A barely-trained GP rating everything alike can't call a pool dead."""
        q = _fake_qualifier(class_counts=(6, 2), probs=[0.02, 0.02], n_obs=9)
        assert _needs_more_discovery(q, _cands()) is False


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
