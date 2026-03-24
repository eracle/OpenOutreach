# tests/db/test_lazy_enrichment.py
"""Tests for Lead lazy accessors (get_profile, get_embedding)."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import numpy as np
import pytest


FAKE_PROFILE = {
    "first_name": "Alice",
    "last_name": "Smith",
    "headline": "Engineer at Acme",
    "positions": [{"company_name": "Acme Corp"}],
    "urn": "urn:li:fsd_profile:ABC123",
}


class TestGetProfile:
    def test_returns_cached(self, fake_session):
        """Returns parsed JSON when description already set."""
        from crm.models import Lead

        lead = Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/alice/",
            public_identifier="alice",
            profile_data=FAKE_PROFILE,
        )

        with patch("linkedin.api.client.PlaywrightLinkedinAPI") as MockAPI:
            result = lead.get_profile(fake_session)
            MockAPI.assert_not_called()

        assert result["first_name"] == "Alice"

    def test_enriches_when_empty(self, fake_session):
        """Calls Voyager API and populates lead when description is empty."""
        from crm.models import Lead

        lead = Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/alice/",
            public_identifier="alice",
        )

        with patch(
            "linkedin.api.client.PlaywrightLinkedinAPI"
        ) as MockAPI:
            MockAPI.return_value.get_profile.return_value = (FAKE_PROFILE, {})
            result = lead.get_profile(fake_session)

        assert result is not None
        assert result["first_name"] == "Alice"
        lead.refresh_from_db()
        assert lead.first_name == "Alice"
        assert lead.profile_data

    def test_crashes_on_api_failure(self, fake_session):
        """Lets API errors propagate (get_profile has its own retry)."""
        from crm.models import Lead

        lead = Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/alice/",
            public_identifier="alice",
        )

        with patch(
            "linkedin.api.client.PlaywrightLinkedinAPI"
        ) as MockAPI:
            MockAPI.return_value.get_profile.side_effect = IOError("timeout")
            with pytest.raises(IOError):
                lead.get_profile(fake_session)


class TestGetUrn:
    def test_extracts_urn(self, fake_session):
        """Returns URN from cached profile."""
        from crm.models import Lead

        lead = Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/alice/",
            public_identifier="alice",
            profile_data=FAKE_PROFILE,
        )

        assert lead.get_urn(fake_session) == "urn:li:fsd_profile:ABC123"


class TestGetEmbedding:
    def test_returns_cached(self, fake_session, db):
        """Returns existing embedding without recomputing."""
        from crm.models import Lead

        emb = np.ones(384, dtype=np.float32)
        lead = Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/alice/",
            public_identifier="alice",
            embedding=emb.tobytes(),
        )

        with patch("linkedin.ml.embeddings.embed_text") as mock:
            result = lead.get_embedding(fake_session)
            mock.assert_not_called()

        np.testing.assert_array_almost_equal(result, emb)

    def test_enriches_and_embeds(self, fake_session, db):
        """Fetches profile and computes embedding when both are missing."""
        from crm.models import Lead

        lead = Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/alice/",
            public_identifier="alice",
        )

        fake_emb = np.ones(384, dtype=np.float32)

        with patch(
            "linkedin.api.client.PlaywrightLinkedinAPI"
        ) as MockAPI, patch(
            "linkedin.ml.embeddings.embed_text",
            return_value=fake_emb,
        ):
            MockAPI.return_value.get_profile.return_value = (FAKE_PROFILE, {})
            result = lead.get_embedding(fake_session)

        assert result is not None
        np.testing.assert_array_almost_equal(result, fake_emb)

    def test_crashes_on_api_failure(self, fake_session, db):
        """Lets API errors propagate."""
        from crm.models import Lead

        lead = Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/alice/",
            public_identifier="alice",
        )

        with patch(
            "linkedin.api.client.PlaywrightLinkedinAPI"
        ) as MockAPI:
            MockAPI.return_value.get_profile.side_effect = IOError("timeout")
            with pytest.raises(IOError):
                lead.get_embedding(fake_session)
