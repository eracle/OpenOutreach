# tests/db/test_lazy_enrichment.py
"""Tests for Lead.resolve_email — BetterContact lookup gated behind a one-time cache.

Post-pivot the live-scrape accessors (get_profile / get_urn / get_embedding /
capture_contact_info) are gone: leads carry their embedding + profile_text from
discovery, and the only remaining paid accessor is the email finder."""
from __future__ import annotations

from unittest.mock import patch

import pytest


class TestResolveEmail:
    """Lead.resolve_email — BetterContact lookup gated behind a one-time cache."""

    def _lead(self, **kwargs):
        from openoutreach.crm.models import Lead

        return Lead.objects.create(
            profile_url="https://www.linkedin.com/in/bob/",
            **kwargs,
        )

    def test_hit_persists_email(self, db):
        from openoutreach.crm.models import Lead
        from openoutreach.emails.bettercontact import BetterContactQuery, BetterContactResult

        lead = self._lead()
        with patch(
            "openoutreach.emails.bettercontact.resolve_email",
            return_value=BetterContactResult(email="bob@acme.com", status="valid"),
        ) as resolve:
            assert lead.resolve_email() is True

        resolve.assert_called_once_with(BetterContactQuery(linkedin_url="https://www.linkedin.com/in/bob/"))
        assert Lead.objects.get(pk=lead.pk).email == "bob@acme.com"

    def test_genuine_miss_returns_false(self, db):
        from openoutreach.crm.models import Lead

        lead = self._lead()
        with patch("openoutreach.emails.bettercontact.resolve_email", return_value=None):
            assert lead.resolve_email() is False

        assert Lead.objects.get(pk=lead.pk).email is None

    def test_bettercontact_unavailable_returns_none(self, db):
        from openoutreach.crm.models import Lead
        from openoutreach.emails.bettercontact import BetterContactUnavailable

        lead = self._lead()
        with patch("openoutreach.emails.bettercontact.resolve_email", side_effect=BetterContactUnavailable("no key")):
            assert lead.resolve_email() is None

        assert Lead.objects.get(pk=lead.pk).email is None

    def test_already_resolved_is_noop(self, db):
        lead = self._lead(email="old@acme.com")
        with patch("openoutreach.emails.bettercontact.resolve_email") as resolve:
            assert lead.resolve_email() is True

        resolve.assert_not_called()
        assert lead.email == "old@acme.com"
