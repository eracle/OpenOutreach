# tests/test_qualify.py
"""Tests for the qualification logic in qualify module."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from openoutreach.linkedin.pipeline.qualify import run_qualification
from openoutreach.linkedin.ml.qualifier import BayesianQualifier


def _make_trained_qualifier(seed=42):
    qualifier = BayesianQualifier(seed=seed)
    rng = np.random.RandomState(seed)
    for _ in range(5):
        qualifier.update(rng.randn(384).astype(np.float32) + 1.0, 1)
        qualifier.update(rng.randn(384).astype(np.float32) - 1.0, 0)
    return qualifier


def _create_lead_with_embedding(lead_id, public_id):
    from openoutreach.crm.models import Lead
    emb = np.ones(384, dtype=np.float32)
    return Lead.objects.create(
        pk=lead_id,
        public_identifier=public_id,
        linkedin_url=f"https://linkedin.com/in/{public_id}/",
        embedding=emb.tobytes(),
    )


def _fake_leads(lead_id=1, public_id="alice"):
    """Return a list matching get_leads_for_qualification output."""
    return [{"lead_id": lead_id, "public_identifier": public_id, "url": "", "profile": {}}]


class TestQualifyAutoDecisions:
    def test_always_calls_llm(self, db):
        qualifier = _make_trained_qualifier()
        session = MagicMock()
        _create_lead_with_embedding(1, "alice")

        with (
            patch("openoutreach.linkedin.db.leads.get_leads_for_qualification", return_value=_fake_leads()),
            patch("openoutreach.linkedin.pipeline.qualify._fetch_profile_text", return_value="engineer at acme"),
            patch("openoutreach.linkedin.ml.qualifier.qualify_with_llm", return_value=(1, "Good fit")) as mock_llm,
            patch.object(qualifier, "update"),
            patch("openoutreach.linkedin.db.leads.promote_lead_to_deal"),
        ):
            run_qualification(session, qualifier)
            mock_llm.assert_called_once()

    def test_llm_on_cold_start(self, db):
        qualifier = BayesianQualifier(seed=42)
        session = MagicMock()
        _create_lead_with_embedding(1, "alice")

        with (
            patch("openoutreach.linkedin.db.leads.get_leads_for_qualification", return_value=_fake_leads()),
            patch("openoutreach.linkedin.pipeline.qualify._fetch_profile_text", return_value="engineer at acme"),
            patch("openoutreach.linkedin.ml.qualifier.qualify_with_llm", return_value=(0, "Bad fit")) as mock_llm,
            patch.object(qualifier, "update"),
            patch("openoutreach.core.db.deals.create_disqualified_deal"),
        ):
            run_qualification(session, qualifier)
            mock_llm.assert_called_once()

    def test_disqualify_on_promote_failure(self, db):
        qualifier = _make_trained_qualifier()
        session = MagicMock()
        _create_lead_with_embedding(1, "alice")

        with (
            patch("openoutreach.linkedin.db.leads.get_leads_for_qualification", return_value=_fake_leads()),
            patch("openoutreach.linkedin.pipeline.qualify._fetch_profile_text", return_value="engineer at acme"),
            patch("openoutreach.linkedin.ml.qualifier.qualify_with_llm", return_value=(1, "Good fit")),
            patch.object(qualifier, "update"),
            patch("openoutreach.linkedin.db.leads.promote_lead_to_deal",
                  side_effect=ValueError("no company_name")),
            patch("openoutreach.core.db.deals.create_disqualified_deal") as mock_disqualify,
        ):
            run_qualification(session, qualifier)
            mock_disqualify.assert_called_once()

    def test_qualified_lead_is_enriched(self, db):
        qualifier = _make_trained_qualifier()
        session = MagicMock()
        _create_lead_with_embedding(1, "alice")

        with (
            patch("openoutreach.linkedin.db.leads.get_leads_for_qualification", return_value=_fake_leads()),
            patch("openoutreach.linkedin.pipeline.qualify._fetch_profile_text", return_value="engineer at acme"),
            patch("openoutreach.linkedin.ml.qualifier.qualify_with_llm", return_value=(1, "Good fit")),
            patch.object(qualifier, "update"),
            patch("openoutreach.linkedin.db.leads.promote_lead_to_deal") as mock_promote,
        ):
            run_qualification(session, qualifier)
            mock_promote.return_value.lead.resolve_api_email.assert_called_once_with()

    def test_finder_hit_routes_to_ready_to_email(self, db):
        """A finder hit (True) routes the Deal QUALIFIED → READY_TO_EMAIL."""
        from openoutreach.crm.models import DealState

        qualifier = _make_trained_qualifier()
        session = MagicMock()
        _create_lead_with_embedding(1, "alice")

        with (
            patch("openoutreach.linkedin.db.leads.get_leads_for_qualification", return_value=_fake_leads()),
            patch("openoutreach.linkedin.pipeline.qualify._fetch_profile_text", return_value="engineer at acme"),
            patch("openoutreach.linkedin.ml.qualifier.qualify_with_llm", return_value=(1, "Good fit")),
            patch.object(qualifier, "update"),
            patch("openoutreach.linkedin.db.leads.promote_lead_to_deal") as mock_promote,
            patch("openoutreach.core.db.deals.set_profile_state") as mock_set_state,
            patch("openoutreach.contacts.service.contribute") as mock_contribute,
        ):
            mock_promote.return_value.lead.resolve_api_email.return_value = True
            run_qualification(session, qualifier)
            mock_set_state.assert_called_once()
            assert mock_set_state.call_args.args[2] == DealState.READY_TO_EMAIL
            mock_contribute.assert_called_once()  # finder hit → moment-1 give-back

    def test_genuine_email_miss_stays_qualified(self, db):
        """A genuine finder miss (False) leaves the Deal QUALIFIED → the connect funnel."""
        qualifier = _make_trained_qualifier()
        session = MagicMock()
        _create_lead_with_embedding(1, "alice")

        with (
            patch("openoutreach.linkedin.db.leads.get_leads_for_qualification", return_value=_fake_leads()),
            patch("openoutreach.linkedin.pipeline.qualify._fetch_profile_text", return_value="engineer at acme"),
            patch("openoutreach.linkedin.ml.qualifier.qualify_with_llm", return_value=(1, "Good fit")),
            patch.object(qualifier, "update"),
            patch("openoutreach.linkedin.db.leads.promote_lead_to_deal") as mock_promote,
            patch("openoutreach.core.db.deals.set_profile_state") as mock_set_state,
        ):
            mock_promote.return_value.lead.resolve_api_email.return_value = False
            run_qualification(session, qualifier)
            mock_set_state.assert_not_called()

    def test_finder_unavailable_leaves_qualified(self, db):
        """A finder that couldn't run (None) leaves the Deal QUALIFIED — no parking."""
        qualifier = _make_trained_qualifier()
        session = MagicMock()
        _create_lead_with_embedding(1, "alice")

        with (
            patch("openoutreach.linkedin.db.leads.get_leads_for_qualification", return_value=_fake_leads()),
            patch("openoutreach.linkedin.pipeline.qualify._fetch_profile_text", return_value="engineer at acme"),
            patch("openoutreach.linkedin.ml.qualifier.qualify_with_llm", return_value=(1, "Good fit")),
            patch.object(qualifier, "update"),
            patch("openoutreach.linkedin.db.leads.promote_lead_to_deal") as mock_promote,
            patch("openoutreach.core.db.deals.set_profile_state") as mock_set_state,
        ):
            mock_promote.return_value.lead.resolve_api_email.return_value = None
            run_qualification(session, qualifier)
            mock_set_state.assert_not_called()
