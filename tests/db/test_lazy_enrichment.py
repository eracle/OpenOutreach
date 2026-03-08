# tests/db/test_lazy_enrichment.py
"""Tests for lazy enrichment and embedding helpers."""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import numpy as np
import pytest


FAKE_PROFILE = {
    "first_name": "Alice",
    "last_name": "Smith",
    "headline": "Engineer at Acme",
    "positions": [{"company_name": "Acme Corp"}],
}
FAKE_RAW_DATA = {"included": [], "data": {}}


class TestEnsureLeadEnriched:
    def test_already_enriched(self, fake_session):
        """Returns True immediately when lead already has a description."""
        from crm.models import Lead
        from linkedin.db.crm_profiles import ensure_lead_enriched

        lead = Lead.objects.create(
            website="https://www.linkedin.com/in/alice/",
            owner=fake_session.django_user,
            description=json.dumps(FAKE_PROFILE),
        )

        with patch("linkedin.db.crm_profiles._fetch_profile") as mock_fetch:
            assert ensure_lead_enriched(fake_session, lead.pk, "alice") is True
            mock_fetch.assert_not_called()

    def test_enriches_url_only_lead(self, fake_session):
        """Fetches profile via Voyager API and populates the lead."""
        from crm.models import Lead
        from linkedin.db.crm_profiles import ensure_lead_enriched

        lead = Lead.objects.create(
            website="https://www.linkedin.com/in/alice/",
            owner=fake_session.django_user,
        )
        assert not lead.description

        with patch(
            "linkedin.db.crm_profiles._fetch_profile",
            return_value=(FAKE_PROFILE, FAKE_RAW_DATA),
        ):
            assert ensure_lead_enriched(fake_session, lead.pk, "alice") is True

        lead.refresh_from_db()
        assert lead.description
        assert lead.first_name == "Alice"

    def test_returns_false_on_api_failure(self, fake_session):
        """Returns False when Voyager API returns (None, None)."""
        from crm.models import Lead
        from linkedin.db.crm_profiles import ensure_lead_enriched

        lead = Lead.objects.create(
            website="https://www.linkedin.com/in/alice/",
            owner=fake_session.django_user,
        )

        with patch(
            "linkedin.db.crm_profiles._fetch_profile",
            return_value=(None, None),
        ):
            assert ensure_lead_enriched(fake_session, lead.pk, "alice") is False

        lead.refresh_from_db()
        assert not lead.description

    def test_returns_false_for_missing_lead(self, fake_session):
        """Returns False when lead PK doesn't exist."""
        from linkedin.db.crm_profiles import ensure_lead_enriched

        assert ensure_lead_enriched(fake_session, 99999, "nobody") is False


class TestEnsureProfileEmbedded:
    def test_already_embedded(self, fake_session, embeddings_db):
        """Returns True immediately when embedding exists."""
        from linkedin.models import ProfileEmbedding
        from linkedin.db.crm_profiles import ensure_profile_embedded

        emb = np.ones(384, dtype=np.float32)
        ProfileEmbedding.objects.create(
            lead_id=1, public_identifier="alice", embedding=emb.tobytes(),
        )

        with patch("linkedin.ml.embeddings.embed_profile") as mock_embed:
            assert ensure_profile_embedded(1, "alice") is True
            mock_embed.assert_not_called()

    def test_embeds_enriched_lead(self, fake_session, embeddings_db):
        """Creates embedding from lead description."""
        from crm.models import Lead
        from linkedin.db.crm_profiles import ensure_profile_embedded

        Lead.objects.create(
            website="https://www.linkedin.com/in/alice/",
            owner=fake_session.django_user,
            description=json.dumps(FAKE_PROFILE),
            pk=42,
        )

        with patch("linkedin.ml.embeddings.embed_profile", return_value=True) as mock_embed:
            assert ensure_profile_embedded(42, "alice") is True
            mock_embed.assert_called_once_with(42, "alice", FAKE_PROFILE)

    def test_returns_false_without_description(self, fake_session, embeddings_db):
        """Returns False when lead has no description to embed."""
        from crm.models import Lead
        from linkedin.db.crm_profiles import ensure_profile_embedded

        Lead.objects.create(
            website="https://www.linkedin.com/in/alice/",
            owner=fake_session.django_user,
            pk=43,
        )

        assert ensure_profile_embedded(43, "alice") is False


class TestEmbedNextProfileLazyEnrich:
    """Test that embed_one lazily enriches url-only leads."""

    def test_url_only_lead_gets_enriched_then_embedded(self, fake_session, embeddings_db):
        from crm.models import Lead
        from linkedin.pipeline.qualify import embed_one
        from linkedin.ml.qualifier import BayesianQualifier

        # Create a url-only lead (no description)
        Lead.objects.create(
            website="https://www.linkedin.com/in/bob/",
            owner=fake_session.django_user,
            disqualified=False,
            pk=10,
        )

        qualifier = BayesianQualifier(seed=42)

        with (
            patch(
                "linkedin.db.crm_profiles._fetch_profile",
                return_value=(FAKE_PROFILE, FAKE_RAW_DATA),
            ),
            patch(
                "linkedin.ml.embeddings.embed_profile",
                return_value=True,
            ) as mock_embed,
        ):
            result = embed_one(fake_session, qualifier)

        assert result == "bob"
        mock_embed.assert_called_once()

    def test_url_only_lead_skipped_on_api_failure(self, fake_session, embeddings_db):
        from crm.models import Lead
        from linkedin.pipeline.qualify import embed_one
        from linkedin.ml.qualifier import BayesianQualifier

        Lead.objects.create(
            website="https://www.linkedin.com/in/bob/",
            owner=fake_session.django_user,
            disqualified=False,
            pk=11,
        )

        qualifier = BayesianQualifier(seed=42)

        with patch(
            "linkedin.db.crm_profiles._fetch_profile",
            return_value=(None, None),
        ):
            result = embed_one(fake_session, qualifier)

        assert result is None
