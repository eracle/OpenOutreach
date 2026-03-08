# tests/test_pools.py
import pytest
from unittest.mock import patch, MagicMock

import numpy as np

from linkedin.db.crm_profiles import create_enriched_lead, promote_lead_to_contact
from linkedin.ml.qualifier import BayesianQualifier
from linkedin.pipeline.pools import top_above_threshold, get_candidate


SAMPLE_PROFILE = {
    "first_name": "Alice",
    "last_name": "Smith",
    "headline": "Engineer",
    "positions": [{"company_name": "Acme"}],
}


def _make_qualified(session, public_id="alice"):
    url = f"https://www.linkedin.com/in/{public_id}/"
    create_enriched_lead(session, url, SAMPLE_PROFILE)
    promote_lead_to_contact(session, public_id)


@pytest.mark.django_db
class TestTopAboveThreshold:
    @pytest.fixture(autouse=True)
    def _db(self, embeddings_db):
        pass

    def test_filters_by_prob_threshold(self, fake_session):
        _make_qualified(fake_session)
        scorer = BayesianQualifier(seed=42)
        scorer.rank_profiles = lambda profiles, **kw: profiles

        with (
            patch.object(scorer, "predict", return_value=(0.5, 0.1, 0.01)),
            patch.object(scorer, "_load_embedding", return_value=np.ones(384)),
        ):
            assert top_above_threshold(fake_session, scorer, 0.9) is None

    def test_accepts_high_prob(self, fake_session):
        _make_qualified(fake_session)
        scorer = BayesianQualifier(seed=42)
        scorer.rank_profiles = lambda profiles, **kw: profiles

        with (
            patch.object(scorer, "predict", return_value=(0.95, 0.1, 0.01)),
            patch.object(scorer, "_load_embedding", return_value=np.ones(384)),
        ):
            assert top_above_threshold(fake_session, scorer, 0.9) is not None


@pytest.mark.django_db
class TestGetCandidate:
    @pytest.fixture(autouse=True)
    def _db(self, embeddings_db):
        pass

    def test_backfills_then_returns(self, fake_session):
        scorer = BayesianQualifier(seed=42)
        candidate = {"public_identifier": "alice", "profile": SAMPLE_PROFILE}

        with (
            patch("linkedin.pipeline.pools.top_above_threshold", side_effect=[None, candidate]),
            patch("linkedin.pipeline.pools.embed_one", return_value="alice"),
        ):
            assert get_candidate(fake_session, scorer, 0.9) == candidate

    def test_exhausted_returns_none(self, fake_session):
        scorer = BayesianQualifier(seed=42)

        with (
            patch("linkedin.pipeline.pools.top_above_threshold", return_value=None),
            patch("linkedin.pipeline.pools.embed_one", return_value=None),
            patch("linkedin.pipeline.pools.qualify_one", return_value=None),
            patch("linkedin.pipeline.pools.search_one", return_value=None),
        ):
            assert get_candidate(fake_session, scorer, 0.9) is None

    def test_partner_skips_backfill(self, fake_session):
        scorer = BayesianQualifier(seed=42)

        with (
            patch("linkedin.pipeline.pools.top_above_threshold", return_value=None),
            patch("linkedin.pipeline.pools.embed_one") as mock_embed,
        ):
            assert get_candidate(fake_session, scorer, 0.9, is_partner=True) is None
            mock_embed.assert_not_called()

    def test_backfill_chain_embed_then_qualify_then_search(self, fake_session):
        scorer = BayesianQualifier(seed=42)

        # embed returns None, qualify returns None, search returns keyword
        with (
            patch("linkedin.pipeline.pools.top_above_threshold", side_effect=[None, None]),
            patch("linkedin.pipeline.pools.embed_one", return_value=None),
            patch("linkedin.pipeline.pools.qualify_one", return_value=None),
            patch("linkedin.pipeline.pools.search_one", return_value="ML engineer"),
        ):
            # search found keyword but no candidate above threshold
            assert get_candidate(fake_session, scorer, 0.9) is None
