# tests/test_qualify.py
"""Tests for run_qualification — the qualify leg of the lazy chain.

Post-pivot, qualify only promotes (label=1) or disqualifies (label=0/promote
failure). Enrichment / email-resolution moved to the find-email leg. Leads carry
their own ``profile_text`` + embedding from discovery — no live scrape."""
from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from openoutreach.core.ml.qualifier import BayesianQualifier
from openoutreach.core.pipeline.qualify import run_qualification


def _make_lead(profile_url="https://www.linkedin.com/in/alice/", profile_text="engineer at acme"):
    from openoutreach.crm.models import Lead

    return Lead.objects.create(
        profile_url=profile_url,
        profile_text=profile_text,
        embedding=np.ones(384, dtype=np.float32).tobytes(),
    )


@pytest.mark.django_db
class TestRunQualification:
    def test_calls_llm_on_candidate_with_profile_text(self, fake_session):
        _make_lead()
        qualifier = BayesianQualifier(seed=42)

        with (
            patch("openoutreach.core.ml.qualifier.qualify_with_llm",
                  return_value=(1, "Good fit")) as mock_llm,
            patch("openoutreach.core.db.leads.promote_lead_to_deal"),
        ):
            result = run_qualification(fake_session, qualifier)

        mock_llm.assert_called_once()
        assert result == "https://www.linkedin.com/in/alice/"

    def test_skips_when_profile_text_empty(self, fake_session):
        _make_lead(profile_text="")
        qualifier = BayesianQualifier(seed=42)

        with (
            patch("openoutreach.core.ml.qualifier.qualify_with_llm") as mock_llm,
            patch("openoutreach.core.db.leads.promote_lead_to_deal") as mock_promote,
        ):
            result = run_qualification(fake_session, qualifier)

        assert result is None
        mock_llm.assert_not_called()
        mock_promote.assert_not_called()

    def test_promotes_on_label_1(self, fake_session):
        _make_lead()
        qualifier = BayesianQualifier(seed=42)

        with (
            patch("openoutreach.core.ml.qualifier.qualify_with_llm", return_value=(1, "Good fit")),
            patch("openoutreach.core.db.leads.promote_lead_to_deal") as mock_promote,
            patch("openoutreach.core.db.deals.create_disqualified_deal") as mock_disq,
        ):
            run_qualification(fake_session, qualifier)

        mock_promote.assert_called_once()
        mock_disq.assert_not_called()

    def test_disqualifies_on_label_0(self, fake_session):
        _make_lead()
        qualifier = BayesianQualifier(seed=42)

        with (
            patch("openoutreach.core.ml.qualifier.qualify_with_llm", return_value=(0, "Bad fit")),
            patch("openoutreach.core.db.leads.promote_lead_to_deal") as mock_promote,
            patch("openoutreach.core.db.deals.create_disqualified_deal") as mock_disq,
        ):
            run_qualification(fake_session, qualifier)

        mock_promote.assert_not_called()
        mock_disq.assert_called_once()

    def test_disqualifies_when_promote_raises_value_error(self, fake_session):
        _make_lead()
        qualifier = BayesianQualifier(seed=42)

        with (
            patch("openoutreach.core.ml.qualifier.qualify_with_llm", return_value=(1, "Good fit")),
            patch("openoutreach.core.db.leads.promote_lead_to_deal",
                  side_effect=ValueError("no company_name")),
            patch("openoutreach.core.db.deals.create_disqualified_deal") as mock_disq,
        ):
            run_qualification(fake_session, qualifier)

        mock_disq.assert_called_once()

    def test_returns_none_when_no_candidates(self, fake_session):
        qualifier = BayesianQualifier(seed=42)

        with patch("openoutreach.core.ml.qualifier.qualify_with_llm") as mock_llm:
            assert run_qualification(fake_session, qualifier) is None

        mock_llm.assert_not_called()
