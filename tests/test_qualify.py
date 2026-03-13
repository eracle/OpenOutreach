# tests/test_qualify.py
"""Tests for the qualification logic in qualify module."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from linkedin.pipeline.qualify import run_qualification
from linkedin.ml.qualifier import BayesianQualifier


def _make_trained_qualifier(seed=42):
    qualifier = BayesianQualifier(seed=seed)
    rng = np.random.RandomState(seed)
    for _ in range(5):
        qualifier.update(rng.randn(384).astype(np.float32) + 1.0, 1)
        qualifier.update(rng.randn(384).astype(np.float32) - 1.0, 0)
    return qualifier


def _create_embedding(lead_id, public_id, label=None):
    from linkedin.models import ProfileEmbedding
    emb = np.ones(384, dtype=np.float32)
    return ProfileEmbedding.objects.create(
        lead_id=lead_id,
        public_identifier=public_id,
        embedding=emb.tobytes(),
        label=label,
    )


def _fake_leads(lead_id=1, public_id="alice"):
    """Return a list matching get_leads_for_qualification output."""
    return [{"lead_id": lead_id, "public_identifier": public_id, "url": "", "profile": {}}]


class TestQualifyAutoDecisions:
    def test_always_calls_llm(self, embeddings_db):
        from linkedin.models import ProfileEmbedding

        qualifier = _make_trained_qualifier()
        session = MagicMock()
        _create_embedding(1, "alice")

        with (
            patch("linkedin.db.leads.get_leads_for_qualification", return_value=_fake_leads()),
            patch("linkedin.pipeline.qualify._fetch_profile_text", return_value="engineer at acme"),
            patch("linkedin.ml.qualifier.qualify_with_llm", return_value=(1, "Good fit")) as mock_llm,
            patch.object(qualifier, "update"),
            patch("linkedin.db.leads.promote_lead_to_contact"),
        ):
            run_qualification(session, qualifier)
            mock_llm.assert_called_once()
            assert ProfileEmbedding.objects.get(lead_id=1).label == 1

    def test_llm_on_cold_start(self, embeddings_db):
        qualifier = BayesianQualifier(seed=42)
        session = MagicMock()
        _create_embedding(1, "alice")

        with (
            patch("linkedin.db.leads.get_leads_for_qualification", return_value=_fake_leads()),
            patch("linkedin.pipeline.qualify._fetch_profile_text", return_value="engineer at acme"),
            patch("linkedin.ml.qualifier.qualify_with_llm", return_value=(0, "Bad fit")) as mock_llm,
            patch.object(qualifier, "update"),
            patch("linkedin.db.deals.create_disqualified_deal"),
        ):
            run_qualification(session, qualifier)
            mock_llm.assert_called_once()

    def test_disqualify_on_promote_failure(self, embeddings_db):
        qualifier = _make_trained_qualifier()
        session = MagicMock()
        _create_embedding(1, "alice")

        with (
            patch("linkedin.db.leads.get_leads_for_qualification", return_value=_fake_leads()),
            patch("linkedin.pipeline.qualify._fetch_profile_text", return_value="engineer at acme"),
            patch("linkedin.ml.qualifier.qualify_with_llm", return_value=(1, "Good fit")),
            patch.object(qualifier, "update"),
            patch("linkedin.db.leads.promote_lead_to_contact",
                  side_effect=ValueError("no Company")),
            patch("linkedin.db.deals.create_disqualified_deal") as mock_disqualify,
        ):
            run_qualification(session, qualifier)
            mock_disqualify.assert_called_once()
