# tests/test_lookup.py
"""The paid lookup, in two steps: buy the address, then check on it.

The backoff is the part worth pinning down. It lives on the deal now
(``not_before`` + ``lookup_attempt``), so a job that never terminates delays that
one lead and nothing else — the same backoff in a shared queue row once put a live
install to sleep for 34 hours.
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from openoutreach.crm.models import DealState
from openoutreach.enrichment.bettercontact import BetterContactUnavailable, PollOutcome
from openoutreach.enrichment.lookup import buy_address, check_lookup, reclaim_lookup
from tests.factories import DealFactory, LeadFactory


def _ready_to_find(campaign, email=None):
    return DealFactory(
        campaign=campaign,
        lead=LeadFactory(email=email),
        state=DealState.READY_TO_FIND_EMAIL,
    )


def _in_flight(campaign, attempt=0, request_id="req1"):
    return DealFactory(
        campaign=campaign,
        lead=LeadFactory(),
        state=DealState.FINDING_EMAIL,
        lookup_request_id=request_id,
        lookup_attempt=attempt,
    )


# ── buy_address ───────────────────────────────────────────────────


@pytest.mark.django_db
class TestBuyAddress:
    def test_a_known_address_skips_the_hub_and_the_provider(self, campaign):
        deal = _ready_to_find(campaign, email="known@corp.com")

        with patch("openoutreach.contacts.service.resolve") as resolve, \
                patch("openoutreach.enrichment.bettercontact.submit") as submit:
            assert buy_address(deal) == DealState.RESOLVED

        resolve.assert_not_called()
        submit.assert_not_called()

    def test_a_hub_hit_skips_the_paid_job(self, campaign):
        deal = _ready_to_find(campaign)

        with patch("openoutreach.contacts.service.resolve", return_value="hub@corp.com"), \
                patch("openoutreach.enrichment.bettercontact.submit") as submit:
            assert buy_address(deal) == DealState.RESOLVED

        submit.assert_not_called()
        deal.lead.refresh_from_db()
        assert deal.lead.email == "hub@corp.com"

    def test_a_hub_miss_submits_and_parks_on_the_handle(self, campaign):
        deal = _ready_to_find(campaign)

        with patch("openoutreach.contacts.service.resolve", return_value=None), \
                patch("openoutreach.enrichment.bettercontact.is_configured", return_value=True), \
                patch("openoutreach.enrichment.bettercontact.submit", return_value="req-42"):
            assert buy_address(deal) == DealState.FINDING_EMAIL

        assert deal.lookup_request_id == "req-42"
        assert deal.lookup_attempt == 0
        assert deal.not_before > timezone.now()

    def test_an_unconfigured_finder_leaves_the_deal_queued_but_backs_it_off(self, campaign):
        """**Queued is not the same as due.** A deal we could not submit has to stop
        being eligible, or the next pass picks the same one and a bounded job never
        returns — noise every few seconds under the old daemon, an endless run now.
        `not_before` is the architecture's one waiting mechanism and this is exactly the
        case it exists for."""
        deal = _ready_to_find(campaign)

        with patch("openoutreach.contacts.service.resolve", return_value=None), \
                patch("openoutreach.enrichment.bettercontact.is_configured", return_value=False), \
                patch("openoutreach.enrichment.bettercontact.submit") as submit:
            assert buy_address(deal) is None

        submit.assert_not_called()
        assert deal.lookup_request_id == ""
        assert deal.not_before > timezone.now()

    def test_a_known_address_resolves_with_no_provider_key_at_all(self, campaign):
        """**The free sources are not gated on the paid one.** An address already on the
        lead costs nothing to use, so an operator with no key — or one whose credits ran
        out — still gets it. The gate belongs on the paid leg, and this is the case that
        says why: a missing key used to switch off the reads that were already free.
        """
        deal = _ready_to_find(campaign, email="known@corp.com")

        with patch("openoutreach.enrichment.bettercontact.is_configured", return_value=False), \
                patch("openoutreach.enrichment.bettercontact.submit") as submit:
            assert buy_address(deal) == DealState.RESOLVED

        submit.assert_not_called()

    def test_the_hub_cache_still_resolves_with_no_provider_key(self, campaign):
        """The cross-operator cache is free and is exactly what an operator out of
        credits has left. Reaching it must not require the thing they have run out of.
        """
        deal = _ready_to_find(campaign)

        with patch("openoutreach.contacts.service.resolve", return_value="hub@corp.com"), \
                patch("openoutreach.enrichment.bettercontact.is_configured", return_value=False), \
                patch("openoutreach.enrichment.bettercontact.submit") as submit:
            assert buy_address(deal) == DealState.RESOLVED

        submit.assert_not_called()
        deal.lead.refresh_from_db()
        assert deal.lead.email == "hub@corp.com"

    def test_an_outage_spends_no_credit_and_backs_the_deal_off(self, campaign):
        """No handle exists to poll, so it will be tried again — after a wait that
        doubles, the same one an in-flight poll takes. Two ways of waiting would be two
        retry policies."""
        deal = _ready_to_find(campaign)

        with patch("openoutreach.contacts.service.resolve", return_value=None), \
                patch("openoutreach.enrichment.bettercontact.is_configured", return_value=True), \
                patch("openoutreach.enrichment.bettercontact.submit",
                      side_effect=BetterContactUnavailable("503")):
            assert buy_address(deal) is None

        assert deal.lookup_request_id == ""
        assert deal.lookup_attempt == 1
        assert deal.not_before > timezone.now()


# ── check_lookup ──────────────────────────────────────────────────


@pytest.mark.django_db
class TestCheckLookup:
    def test_a_hit_stores_the_address_and_gives_it_back(self, campaign):
        deal = _in_flight(campaign)

        with patch("openoutreach.enrichment.bettercontact.poll_once",
                   return_value=PollOutcome(running=False, email="found@corp.com")), \
                patch("openoutreach.contacts.service.contribute") as contribute:
            assert check_lookup(deal) == DealState.RESOLVED

        deal.lead.refresh_from_db()
        assert deal.lead.email == "found@corp.com"
        contribute.assert_called_once()
        assert deal.not_before is None
        assert deal.lookup_request_id == ""

    def test_a_hit_also_stores_the_name_the_provider_resolved(self, campaign):
        """First/last arrive with the address, at no extra call or credit.

        This is why nothing in the codebase splits a full name: discovery only ever
        knows one, and the enrichment waterfall knows the real parts.
        """
        deal = _in_flight(campaign)

        with patch("openoutreach.enrichment.bettercontact.poll_once",
                   return_value=PollOutcome(
                       running=False, email="elon@tesla.com",
                       first_name="Elon", last_name="Musk")), \
                patch("openoutreach.contacts.service.contribute"):
            assert check_lookup(deal) == DealState.RESOLVED

        deal.lead.refresh_from_db()
        assert (deal.lead.first_name, deal.lead.last_name) == ("Elon", "Musk")

    def test_a_title_stamped_at_discovery_survives_the_lookup(self, campaign):
        """The qualifier judged the lead on the discovered title; the lookup leaves it."""
        deal = DealFactory(
            campaign=campaign,
            lead=LeadFactory(job_title="Founder"),
            state=DealState.FINDING_EMAIL,
            lookup_request_id="req1",
        )

        with patch("openoutreach.enrichment.bettercontact.poll_once",
                   return_value=PollOutcome(running=False, email="a@b.com")), \
                patch("openoutreach.contacts.service.contribute"):
            check_lookup(deal)

        deal.lead.refresh_from_db()
        assert deal.lead.job_title == "Founder"

    def test_a_hub_cache_hit_leaves_the_name_parts_null(self, campaign):
        """The free hub resolves an address only — no identity, and none invented."""
        deal = _ready_to_find(campaign)

        with patch("openoutreach.contacts.service.resolve", return_value="hub@corp.com"):
            assert buy_address(deal) == DealState.RESOLVED

        deal.lead.refresh_from_db()
        assert deal.lead.email == "hub@corp.com"
        assert deal.lead.first_name is None and deal.lead.last_name is None

    def test_a_miss_is_its_own_terminal(self, campaign):
        """Reachability failed, not fit — the ML labeler keeps the lead positive."""
        deal = _in_flight(campaign)

        with patch("openoutreach.enrichment.bettercontact.poll_once",
                   return_value=PollOutcome(running=False, email="")):
            assert check_lookup(deal) == DealState.NO_EMAIL_FOUND

    def test_a_running_job_backs_off_on_its_own_row(self, campaign):
        deal = _in_flight(campaign, attempt=0)

        with patch("openoutreach.enrichment.bettercontact.poll_once",
                   return_value=PollOutcome(running=True)):
            assert check_lookup(deal) is None

        assert deal.lookup_attempt == 1
        assert deal.not_before > timezone.now()

    def test_the_backoff_doubles_into_days(self, campaign):
        deal = _in_flight(campaign, attempt=15)

        with patch("openoutreach.enrichment.bettercontact.poll_once",
                   return_value=PollOutcome(running=True)):
            check_lookup(deal)

        assert deal.not_before - timezone.now() > timedelta(days=1)

    def test_an_extreme_attempt_count_stays_representable(self, campaign):
        """The rail exists so ``datetime`` can still express the schedule."""
        deal = _in_flight(campaign, attempt=200)

        with patch("openoutreach.enrichment.bettercontact.poll_once",
                   return_value=PollOutcome(running=True)):
            assert check_lookup(deal) is None

        assert deal.not_before is not None

    def test_a_provider_outage_retries_at_the_same_interval(self, campaign):
        """Nothing was learned about the job, so the backoff must not advance."""
        deal = _in_flight(campaign, attempt=3)

        with patch("openoutreach.enrichment.bettercontact.poll_once",
                   side_effect=BetterContactUnavailable("503")):
            assert check_lookup(deal) is None

        assert deal.lookup_attempt == 3
        assert deal.not_before > timezone.now()

    def test_a_stalled_job_is_never_abandoned(self, campaign):
        """Abandoning reverted the deal and bought a *second* job for the same lead."""
        deal = _in_flight(campaign, attempt=40)

        with patch("openoutreach.enrichment.bettercontact.poll_once",
                   return_value=PollOutcome(running=True)):
            assert check_lookup(deal) is None

        assert deal.lookup_request_id == "req1"


# ── reclaim_lookup ────────────────────────────────────────────────


@pytest.mark.django_db
class TestReclaimLookup:
    def test_a_handleless_deal_goes_back_to_be_bought(self, campaign):
        """No request_id means no job and no credit spent — the buy step owns it."""
        deal = _in_flight(campaign, attempt=2, request_id="")
        deal.not_before = timezone.now() - timedelta(hours=1)

        assert reclaim_lookup(deal) == DealState.READY_TO_FIND_EMAIL
        assert deal.not_before is None
        assert deal.lookup_attempt == 0

    def test_it_never_touches_the_provider(self, campaign):
        """There is nothing to poll, and polling an empty handle would spend a call
        to be told so."""
        deal = _in_flight(campaign, request_id="")

        with patch("openoutreach.enrichment.bettercontact.poll_once") as poll:
            reclaim_lookup(deal)

        poll.assert_not_called()
