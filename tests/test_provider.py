# tests/test_provider.py
"""The finder seam: which vendor an install resolves with, and how a lookup lands.

Two properties matter here. **Selection** — a key is all it takes, and the tie-break
only exists for an install holding both. And **interchangeability** — a sync provider
and an async one must leave the deal in the same states with the same contribution,
differing only in whether it passes through FINDING_EMAIL on the way.
"""
from unittest.mock import patch

import pytest

from openoutreach.crm.models import DealState
from openoutreach.enrichment import apollo, bettercontact, provider
from openoutreach.enrichment.lookup import buy_address
from openoutreach.enrichment.provider import Lookup, PollOutcome
from tests.factories import DealFactory, LeadFactory


@pytest.fixture
def config(db):
    from openoutreach.core.models import SiteConfig
    return SiteConfig.load()


def _keys(config, *, bc="", ap="", preferred=""):
    config.bettercontact_api_key = bc
    config.apollo_api_key = ap
    config.email_finder = preferred
    config.save()


def _ready_to_find(campaign):
    return DealFactory(
        campaign=campaign,
        lead=LeadFactory(email=None),
        state=DealState.READY_TO_FIND_EMAIL,
    )


# ── selection ─────────────────────────────────────────────────────


class TestActive:

    def test_no_key_means_no_finder(self, config):
        _keys(config)
        assert provider.active() is None

    def test_one_key_selects_that_vendor_with_no_setting(self, config):
        _keys(config, ap="secret")
        assert provider.active() is apollo

    def test_the_incumbent_wins_an_unset_tie(self, config):
        """Both keys, no preference: an existing install's spend does not move vendor
        on the strength of a stray key."""
        _keys(config, bc="secret", ap="secret")
        assert provider.active() is bettercontact

    def test_the_setting_breaks_the_tie(self, config):
        _keys(config, bc="secret", ap="secret", preferred="apollo")
        assert provider.active() is apollo

    def test_a_preference_without_its_key_selects_nothing(self, config):
        """Silently falling through to the other vendor would spend on an account the
        operator did not choose."""
        _keys(config, bc="secret", preferred="apollo")
        assert provider.active() is None

    def test_a_handle_is_polled_by_the_vendor_that_minted_it(self):
        assert provider.by_name("apollo") is apollo
        assert provider.by_name("bettercontact") is bettercontact
        assert provider.by_name("") is None


# ── interchangeability ────────────────────────────────────────────


class TestEitherFinderResolves:

    def test_a_sync_finder_resolves_without_entering_finding_email(self, config, campaign):
        _keys(config, ap="secret")
        deal = _ready_to_find(campaign)
        hit = Lookup(outcome=PollOutcome(running=False, email="alice@acme.com"))

        with patch.object(apollo, "start", return_value=hit), \
             patch("openoutreach.contacts.service.contribute"):
            assert buy_address(deal) == DealState.RESOLVED

        deal.lead.refresh_from_db()
        assert deal.lead.email == "alice@acme.com"
        assert deal.lookup_request_id == ""

    def test_an_async_finder_parks_on_its_handle(self, config, campaign):
        _keys(config, bc="secret")
        deal = _ready_to_find(campaign)

        with patch.object(bettercontact, "start", return_value=Lookup(request_id="req1")):
            assert buy_address(deal) == DealState.FINDING_EMAIL

        assert deal.lookup_request_id == "req1"
        assert deal.lookup_provider == "bettercontact"

    def test_a_sync_miss_is_the_same_terminal_as_an_async_one(self, config, campaign):
        _keys(config, ap="secret")
        deal = _ready_to_find(campaign)

        with patch.object(apollo, "start", return_value=Lookup(outcome=PollOutcome(running=False))):
            assert buy_address(deal) == DealState.NO_EMAIL_FOUND

    def test_the_contribution_is_stamped_with_the_finder_that_paid(self, config, campaign):
        _keys(config, ap="secret")
        deal = _ready_to_find(campaign)
        hit = Lookup(outcome=PollOutcome(running=False, email="alice@acme.com"))

        with patch.object(apollo, "start", return_value=hit), \
             patch("openoutreach.contacts.service.contribute") as contribute:
            buy_address(deal)

        assert contribute.call_args.args[2] == "apollo"

    def test_the_free_sources_still_come_first(self, config, campaign):
        """The hub cache must not spend a credit, whichever vendor is configured."""
        _keys(config, ap="secret")
        deal = _ready_to_find(campaign)

        with patch("openoutreach.contacts.service.resolve", return_value="free@acme.com"), \
             patch.object(apollo, "start") as start:
            assert buy_address(deal) == DealState.RESOLVED

        start.assert_not_called()
