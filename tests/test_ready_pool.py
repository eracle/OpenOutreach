# tests/test_ready_pool.py
import pytest
from unittest.mock import patch

import numpy as np

from linkedin.db.deals import set_profile_state
from linkedin.db.leads import create_enriched_lead, promote_lead_to_deal
from linkedin.ml.qualifier import BayesianQualifier
from linkedin.enums import ProfileState
from linkedin.pipeline.ready_pool import promote_to_ready, find_ready_candidate


SAMPLE_PROFILE = {
    "first_name": "Alice",
    "last_name": "Smith",
    "headline": "Engineer",
    "positions": [{"company_name": "Acme"}],
}


def _make_qualified(session, public_id="alice"):
    url = f"https://www.linkedin.com/in/{public_id}/"
    create_enriched_lead(session, url, SAMPLE_PROFILE)
    promote_lead_to_deal(session, public_id)


@pytest.mark.django_db
class TestPromoteToReady:
    @pytest.fixture(autouse=True)
    def _db(self, embeddings_db):
        pass

    def test_promotes_above_threshold(self, fake_session):
        _make_qualified(fake_session, "alice")
        _make_qualified(fake_session, "bob")

        scorer = BayesianQualifier(seed=42)

        with patch(
            "linkedin.pipeline.ready_pool.load_embedding",
            return_value=np.ones(384),
        ), patch.object(
            scorer, "predict_probs", return_value=np.array([0.95, 0.80]),
        ):
            count = promote_to_ready(fake_session, scorer, threshold=0.9)

        assert count == 1

        from crm.models import Deal
        alice_deal = Deal.objects.get(lead__linkedin_url="https://www.linkedin.com/in/alice/")
        bob_deal = Deal.objects.get(lead__linkedin_url="https://www.linkedin.com/in/bob/")
        assert alice_deal.state == ProfileState.READY_TO_CONNECT
        assert bob_deal.state == ProfileState.QUALIFIED

    def test_returns_zero_on_cold_start(self, fake_session):
        _make_qualified(fake_session)

        scorer = BayesianQualifier(seed=42)

        with patch(
            "linkedin.pipeline.ready_pool.load_embedding",
            return_value=np.ones(384),
        ), patch.object(
            scorer, "predict_probs", return_value=None,
        ):
            assert promote_to_ready(fake_session, scorer, threshold=0.9) == 0

    def test_returns_zero_on_empty_pool(self, fake_session):
        scorer = BayesianQualifier(seed=42)
        assert promote_to_ready(fake_session, scorer, threshold=0.9) == 0


@pytest.mark.django_db
class TestGetReadyCandidate:
    @pytest.fixture(autouse=True)
    def _db(self, embeddings_db):
        pass

    def test_returns_none_when_empty(self, fake_session):
        scorer = BayesianQualifier(seed=42)
        assert find_ready_candidate(fake_session, scorer) is None

    def test_returns_top_ranked(self, fake_session):
        _make_qualified(fake_session, "alice")
        set_profile_state(fake_session, "alice", ProfileState.READY_TO_CONNECT.value)

        scorer = BayesianQualifier(seed=42)
        scorer.rank_profiles = lambda profiles, **kw: profiles

        result = find_ready_candidate(fake_session, scorer)
        assert result is not None
        assert result["public_identifier"] == "alice"

    def test_falls_back_to_fifo_when_ranker_cold_starts(self, fake_session):
        _make_qualified(fake_session, "alice")
        _make_qualified(fake_session, "bob")
        set_profile_state(fake_session, "alice", ProfileState.READY_TO_CONNECT.value)
        set_profile_state(fake_session, "bob", ProfileState.READY_TO_CONNECT.value)

        scorer = BayesianQualifier(seed=42)
        scorer.rank_profiles = lambda profiles, **kw: []

        result = find_ready_candidate(fake_session, scorer)
        assert result is not None
        assert result["public_identifier"] == "alice"
