# tests/test_ready_pool.py
"""Find-email pool: the GP rank gate promoting QUALIFIED → READY_TO_FIND_EMAIL."""
import pytest
from unittest.mock import patch

import numpy as np

from openoutreach.core.db.deals import set_profile_state
from openoutreach.core.db.leads import promote_lead_to_deal
from openoutreach.core.ml.qualifier import BayesianQualifier
from openoutreach.core.pipeline.ready_pool import promote_to_ready, find_ready_candidate
from openoutreach.crm.models import DealState


def _make_qualified(session, slug="alice"):
    """Create an embedded Lead and a QUALIFIED Deal for it. Returns the profile_url."""
    from openoutreach.crm.models import Lead

    url = f"https://www.linkedin.com/in/{slug}/"
    Lead.objects.create(
        profile_url=url,
        profile_text="engineer at acme",
        embedding=np.ones(384, dtype=np.float32).tobytes(),
    )
    promote_lead_to_deal(session, url)
    return url


@pytest.mark.django_db
class TestPromoteToReady:
    def test_promotes_above_threshold(self, fake_session):
        alice_url = _make_qualified(fake_session, "alice")
        bob_url = _make_qualified(fake_session, "bob")

        scorer = BayesianQualifier(seed=42)

        with patch.object(scorer, "predict_probs", return_value=np.array([0.95, 0.80])):
            count = promote_to_ready(fake_session, scorer, threshold=0.9)

        assert count == 1

        from openoutreach.crm.models import Deal
        alice_deal = Deal.objects.get(lead__profile_url=alice_url)
        bob_deal = Deal.objects.get(lead__profile_url=bob_url)
        assert alice_deal.state == DealState.READY_TO_FIND_EMAIL
        assert bob_deal.state == DealState.QUALIFIED

    def test_returns_zero_on_cold_start(self, fake_session):
        _make_qualified(fake_session)

        scorer = BayesianQualifier(seed=42)

        with patch.object(scorer, "predict_probs", return_value=None):
            assert promote_to_ready(fake_session, scorer, threshold=0.9) == 0

    def test_returns_zero_on_empty_pool(self, fake_session):
        scorer = BayesianQualifier(seed=42)
        assert promote_to_ready(fake_session, scorer, threshold=0.9) == 0


@pytest.mark.django_db
class TestFindReadyCandidate:
    def test_returns_none_when_empty(self, fake_session):
        scorer = BayesianQualifier(seed=42)
        assert find_ready_candidate(fake_session, scorer) is None

    def test_returns_top_ranked(self, fake_session):
        url = _make_qualified(fake_session, "alice")
        set_profile_state(fake_session, url, DealState.READY_TO_FIND_EMAIL.value)

        scorer = BayesianQualifier(seed=42)
        scorer.rank_profiles = lambda profiles: profiles

        result = find_ready_candidate(fake_session, scorer)
        assert result is not None
        assert result["profile_url"] == url
