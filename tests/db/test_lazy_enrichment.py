# tests/db/test_lazy_enrichment.py
"""Tests for Lead lazy accessors (get_profile, get_urn, get_embedding)."""
from __future__ import annotations

from unittest.mock import patch

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
    def test_live_scrape_every_call(self, fake_session):
        """`get_profile` is a thin live scrape — no DB caching."""
        from openoutreach.crm.models import Lead

        lead = Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/alice/",
            public_identifier="alice",
        )

        with patch("linkedin_cli.api.client.PlaywrightLinkedinAPI") as MockAPI:
            MockAPI.return_value.get_profile.return_value = (FAKE_PROFILE, {})
            result = lead.get_profile(fake_session)
            lead.get_profile(fake_session)

        # Both calls scraped — no memoization at the Lead level.
        assert MockAPI.return_value.get_profile.call_count == 2
        assert result["first_name"] == "Alice"

    def test_populates_urn_from_scrape(self, fake_session):
        """First successful scrape promotes `urn` onto the Lead row."""
        from openoutreach.crm.models import Lead

        lead = Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/alice/",
            public_identifier="alice",
        )
        assert lead.urn is None

        with patch("linkedin_cli.api.client.PlaywrightLinkedinAPI") as MockAPI:
            MockAPI.return_value.get_profile.return_value = (FAKE_PROFILE, {})
            lead.get_profile(fake_session)

        lead.refresh_from_db()
        assert lead.urn == "urn:li:fsd_profile:ABC123"

    def test_returns_none_on_empty_scrape(self, fake_session):
        """An empty Voyager response surfaces as None (no exception)."""
        from openoutreach.crm.models import Lead

        lead = Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/alice/",
            public_identifier="alice",
        )

        with patch("linkedin_cli.api.client.PlaywrightLinkedinAPI") as MockAPI:
            MockAPI.return_value.get_profile.return_value = (None, {})
            assert lead.get_profile(fake_session) is None

    def test_crashes_on_api_failure(self, fake_session):
        """Lets API errors propagate (get_profile has its own retry)."""
        from openoutreach.crm.models import Lead

        lead = Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/alice/",
            public_identifier="alice",
        )

        with patch("linkedin_cli.api.client.PlaywrightLinkedinAPI") as MockAPI:
            MockAPI.return_value.get_profile.side_effect = IOError("timeout")
            with pytest.raises(IOError):
                lead.get_profile(fake_session)


class TestGetUrn:
    def test_reads_cached_column_without_scraping(self, fake_session):
        """If `urn` is already cached on the row, no scrape happens."""
        from openoutreach.crm.models import Lead

        lead = Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/alice/",
            public_identifier="alice",
            urn="urn:li:fsd_profile:ABC123",
        )

        with patch("linkedin_cli.api.client.PlaywrightLinkedinAPI") as MockAPI:
            assert lead.get_urn(fake_session) == "urn:li:fsd_profile:ABC123"
            MockAPI.assert_not_called()

    def test_scrapes_when_column_is_null(self, fake_session):
        """Missing `urn` triggers a live scrape and caches the result."""
        from openoutreach.crm.models import Lead

        lead = Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/alice/",
            public_identifier="alice",
        )

        with patch("linkedin_cli.api.client.PlaywrightLinkedinAPI") as MockAPI:
            MockAPI.return_value.get_profile.return_value = (FAKE_PROFILE, {})
            assert lead.get_urn(fake_session) == "urn:li:fsd_profile:ABC123"

        lead.refresh_from_db()
        assert lead.urn == "urn:li:fsd_profile:ABC123"


class TestGetEmbedding:
    def test_returns_cached(self, fake_session, db):
        """Returns existing embedding without recomputing."""
        from openoutreach.crm.models import Lead

        emb = np.ones(384, dtype=np.float32)
        lead = Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/alice/",
            public_identifier="alice",
            embedding=emb.tobytes(),
        )

        with patch("openoutreach.linkedin.ml.embeddings.embed_text") as mock:
            result = lead.get_embedding(fake_session)
            mock.assert_not_called()

        np.testing.assert_array_almost_equal(result, emb)

    def test_enriches_and_embeds(self, fake_session, db):
        """Fetches profile and computes embedding when both are missing."""
        from openoutreach.crm.models import Lead

        lead = Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/alice/",
            public_identifier="alice",
        )

        fake_emb = np.ones(384, dtype=np.float32)

        with patch("linkedin_cli.api.client.PlaywrightLinkedinAPI") as MockAPI, \
             patch("openoutreach.linkedin.ml.embeddings.embed_text", return_value=fake_emb):
            MockAPI.return_value.get_profile.return_value = (FAKE_PROFILE, {})
            result = lead.get_embedding(fake_session)

        assert result is not None
        np.testing.assert_array_almost_equal(result, fake_emb)

    def test_crashes_on_api_failure(self, fake_session, db):
        """Lets API errors propagate."""
        from openoutreach.crm.models import Lead

        lead = Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/alice/",
            public_identifier="alice",
        )

        with patch("linkedin_cli.api.client.PlaywrightLinkedinAPI") as MockAPI:
            MockAPI.return_value.get_profile.side_effect = IOError("timeout")
            with pytest.raises(IOError):
                lead.get_embedding(fake_session)


class TestResolveApiEmail:
    """Lead.resolve_api_email — finder lookup gated behind a one-time cache."""

    def _lead(self, **kwargs):
        from openoutreach.crm.models import Lead

        return Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/bob/",
            public_identifier="bob",
            **kwargs,
        )

    def test_hit_persists_email(self, db):
        from openoutreach.crm.models import Lead
        from openoutreach.emails.finder import FinderQuery, FinderResult

        lead = self._lead()
        with patch(
            "openoutreach.emails.finder.resolve_email",
            return_value=FinderResult(email="bob@acme.com", status="valid"),
        ) as resolve:
            assert lead.resolve_api_email() is True

        resolve.assert_called_once_with(FinderQuery(linkedin_url="https://www.linkedin.com/in/bob/"))
        assert Lead.objects.get(pk=lead.pk).api_email == "bob@acme.com"

    def test_genuine_miss_returns_false(self, db):
        from openoutreach.crm.models import Lead

        lead = self._lead()
        with patch("openoutreach.emails.finder.resolve_email", return_value=None):
            assert lead.resolve_api_email() is False

        assert Lead.objects.get(pk=lead.pk).api_email is None

    def test_finder_unavailable_returns_none(self, db):
        from openoutreach.crm.models import Lead
        from openoutreach.emails.finder import FinderUnavailable

        lead = self._lead()
        with patch("openoutreach.emails.finder.resolve_email", side_effect=FinderUnavailable("no key")):
            assert lead.resolve_api_email() is None

        assert Lead.objects.get(pk=lead.pk).api_email is None

    def test_already_resolved_is_noop(self, db):
        lead = self._lead(api_email="old@acme.com")
        with patch("openoutreach.emails.finder.resolve_email") as resolve:
            assert lead.resolve_api_email() is True

        resolve.assert_not_called()
        assert lead.api_email == "old@acme.com"
