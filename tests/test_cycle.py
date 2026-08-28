# tests/test_cycle.py
"""The cycle: the hierarchy, the ``not_before`` gate, and running without a mailbox.

The test that matters most is ``test_a_stalled_lookup_gates_only_its_own_row`` — it
is the 2026-08-05 incident written down. A deal's timestamp can gate that deal and
nothing else, which is the whole reason the task queue is gone.

Four rows, down from six. Answering a reply and sending a first email left with the
sending leg, and with them every test that asserted a send outranked something.
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from pydantic_ai.exceptions import ModelHTTPError

from openoutreach.core import cycle
from openoutreach.crm.models import DealState
from tests.factories import DealFactory, LeadFactory


def _deal(campaign, state, **kwargs):
    lead_kwargs = {"email": kwargs.pop("email", None)}
    return DealFactory(
        campaign=campaign, lead=LeadFactory(**lead_kwargs), state=state, **kwargs)


@pytest.fixture
def steps():
    """Every step stubbed, so a test asserts *which* one the cycle chose."""
    with patch("openoutreach.enrichment.lookup.check_lookup",
               return_value=None) as check, \
            patch("openoutreach.core.pipeline.ready_pool.promote_to_ready",
                  return_value=1) as score, \
            patch("openoutreach.core.ml.qualifier.qualifier_for",
                  return_value=object()), \
            patch("openoutreach.enrichment.lookup.buy_address",
                  return_value=None) as buy, \
            patch("openoutreach.core.pipeline.top_up.top_up",
                  return_value=False) as fill:
        yield {"check": check, "score": score, "buy": buy, "top_up": fill}


def _called(steps):
    return {name for name, mock in steps.items() if mock.called}


# ── The hierarchy ─────────────────────────────────────────────────


@pytest.mark.django_db
class TestPriority:
    def test_an_in_flight_lookup_outranks_everything(self, campaign, steps):
        _deal(campaign, DealState.FINDING_EMAIL, lookup_request_id="req1")
        _deal(campaign, DealState.QUALIFIED)

        assert cycle.run_one_action(campaign) is True
        assert _called(steps) == {"check"}

    def test_a_lookup_with_no_job_handle_is_reclaimed_not_stranded(self, campaign, steps):
        """Measured on a live install: two deals sat at FINDING_EMAIL with an empty
        ``request_id`` for 206 hours — the poll row skipped them and no other row
        claims that state."""
        deal = _deal(campaign, DealState.FINDING_EMAIL, lookup_request_id="")

        assert cycle.run_one_action(campaign) is True
        deal.refresh_from_db()
        assert deal.state == DealState.READY_TO_FIND_EMAIL
        assert "check" not in _called(steps)

    def test_ranking_the_pool_outranks_buying_an_address(self, campaign, steps):
        """Rank first: the gate that decides who is worth a credit runs before the
        credit is spent."""
        _deal(campaign, DealState.QUALIFIED)
        _deal(campaign, DealState.READY_TO_FIND_EMAIL)

        with patch("openoutreach.enrichment.bettercontact.is_configured",
                   return_value=True):
            cycle.run_one_action(campaign)
        assert _called(steps) == {"score"}

    def test_buying_an_address_outranks_finding_more_leads(self, campaign, steps):
        _deal(campaign, DealState.READY_TO_FIND_EMAIL)

        with patch("openoutreach.enrichment.bettercontact.is_configured",
                   return_value=True):
            cycle.run_one_action(campaign, buy_addresses=True)
        assert _called(steps) == {"buy"}

    def test_topping_up_is_the_last_resort(self, campaign, steps):
        cycle.run_one_action(campaign)
        assert _called(steps) == {"top_up"}

    def test_an_idle_campaign_with_nothing_to_do_says_so(self, campaign, steps):
        assert cycle.run_one_action(campaign) is False


# ── the paid row ──────────────────────────────────────────────────


@pytest.mark.django_db
class TestBuyingIsOffUnlessAskedFor:
    """``buy_addresses=False`` is the default, and it reaches the paid row.

    A run counting *leads* would otherwise reach the buy row for anything an earlier
    run left past the confidence gate — which is what it used to do, before `--emails`
    made the spend opt-in.
    """

    def test_the_paid_row_is_skipped(self, campaign, steps):
        _deal(campaign, DealState.READY_TO_FIND_EMAIL)

        with patch("openoutreach.enrichment.bettercontact.is_configured",
                   return_value=True):
            cycle.run_one_action(campaign, buy_addresses=False)

        assert "buy" not in _called(steps)

    def test_the_free_rows_still_run(self, campaign, steps):
        """Withholding permission to spend must not turn off the work that costs
        nothing, or the default would just mean "do less"."""
        _deal(campaign, DealState.READY_TO_FIND_EMAIL)

        with patch("openoutreach.enrichment.bettercontact.is_configured",
                   return_value=True):
            cycle.run_one_action(campaign, buy_addresses=False)

        assert _called(steps) == {"top_up"}

    def test_the_row_is_reached_without_a_provider_key(self, campaign, steps):
        """**The row is not a gate on the spend.** It used to decline unless a key was
        configured, which also switched off the free sources inside ``buy_address`` —
        an address in hand and the hub cache — exactly when a free hit was worth most.
        The key is now checked on the paid leg alone, so the row still runs.
        """
        _deal(campaign, DealState.READY_TO_FIND_EMAIL, email="known@corp.com")

        with patch("openoutreach.enrichment.bettercontact.is_configured",
                   return_value=False):
            cycle.run_one_action(campaign, buy_addresses=True)

        assert "buy" in _called(steps)

    def test_an_lookup_already_paid_for_is_still_collected(self, campaign, steps):
        """Abandoning an in-flight lookup would waste a credit already committed
        rather than save one, so the poll row is not a paid row."""
        _deal(campaign, DealState.FINDING_EMAIL, lookup_request_id="req1")

        assert cycle.run_one_action(campaign, buy_addresses=False) is True
        assert _called(steps) == {"check"}


# ── not_before ────────────────────────────────────────────────────


@pytest.mark.django_db
class TestNotBefore:
    def test_a_deal_told_to_wait_is_not_served(self, campaign, steps):
        _deal(campaign, DealState.FINDING_EMAIL, lookup_request_id="req1",
              not_before=timezone.now() + timedelta(hours=1))

        cycle.run_one_action(campaign)
        assert "check" not in _called(steps)

    def test_a_deal_whose_wait_has_elapsed_is_served(self, campaign, steps):
        _deal(campaign, DealState.FINDING_EMAIL, lookup_request_id="req1",
              not_before=timezone.now() - timedelta(seconds=1))

        cycle.run_one_action(campaign)
        assert "check" in _called(steps)

    def test_a_stalled_lookup_gates_only_its_own_row(self, campaign, steps):
        """**The 2026-08-05 incident.** Two lookups had backed off 45 hours; they
        were the only rows in the queue, so the daemon slept 34 hours while 55 ready
        deals waited. A timestamp now gates its own row and nothing else — the work
        below it in the hierarchy runs regardless."""
        for i in range(2):
            _deal(campaign, DealState.FINDING_EMAIL, lookup_request_id=f"req{i}",
                  not_before=timezone.now() + timedelta(hours=45))
        _deal(campaign, DealState.QUALIFIED)

        assert cycle.run_one_action(campaign) is True
        assert _called(steps) == {"score"}


# ── The finder needs nothing but a key ────────────────────────────


@pytest.mark.django_db
class TestTheFinderRunsWithoutASender:
    """The pivot, in tests: the product finds leads, and it has no sending leg at all.

    These replace ``TestRoomToSendToday``. That gate — *never buy an address, and
    never qualify a lead, for someone there is no room to email today* — was correct
    while every lead ended in a send. It also meant an install with no ``Mailbox`` row
    had zero pool headroom, so discovery and qualification never ran: the daemon
    looked alive and produced nothing, with no error and no log line saying why. There
    is no ``Mailbox`` model any more, so the silence cannot come back — but the rule it
    stood for is worth asserting, because the coupling could be reintroduced by
    anything that gates a free step on a paid one.
    """

    def test_discovery_and_qualification_are_ungated(self, campaign, steps):
        """The one that would have failed before the cut: an install with nothing
        configured but an LLM key still fires top-up."""
        cycle.run_one_action(campaign)
        assert "top_up" in _called(steps)

    def test_finding_leads_does_not_need_the_paid_provider(self, campaign, steps):
        """Discovery is free and qualification costs one LLM call, so neither waits
        on the enrichment key. Only row 3 does."""
        _deal(campaign, DealState.READY_TO_FIND_EMAIL)

        with patch("openoutreach.enrichment.bettercontact.is_configured",
                   return_value=False):
            cycle.run_one_action(campaign)
        assert _called(steps) == {"top_up"}

    def test_addresses_are_bought_for_the_export_not_for_a_send(self, campaign, steps):
        """Enrichment is the finder's own leg: a resolved address is a column in the
        export, not a prerequisite for a send that will never happen."""
        _deal(campaign, DealState.READY_TO_FIND_EMAIL)

        with patch("openoutreach.enrichment.bettercontact.is_configured",
                   return_value=True):
            assert cycle.run_one_action(campaign, buy_addresses=True) is True
        assert _called(steps) == {"buy"}

    def test_no_finder_key_means_no_buying(self, campaign, steps):
        """The one gate left on the paid row, and it is about the provider, not the
        pipeline: with no key there is nobody to submit the job to."""
        _deal(campaign, DealState.READY_TO_FIND_EMAIL)

        with patch("openoutreach.enrichment.bettercontact.is_configured",
                   return_value=False):
            cycle.run_one_action(campaign)
        assert "buy" not in _called(steps)


# ── Failure handling ──────────────────────────────────────────────


@pytest.mark.django_db
class TestFailures:
    def test_an_ordinary_failure_leaves_the_row_untouched(self, campaign):
        """The cycle's try/except is a bug backstop: log, skip, keep going."""
        deal = _deal(campaign, DealState.FINDING_EMAIL, lookup_request_id="req1")

        with patch("openoutreach.enrichment.lookup.check_lookup",
                   side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                cycle.run_one_action(campaign)

        deal.refresh_from_db()
        assert deal.state == DealState.FINDING_EMAIL
        assert deal.not_before is None

    def test_a_halting_error_is_not_swallowed(self, campaign):
        """A bad LLM key must stop the daemon loudly, or it retries every five
        seconds forever while looking alive."""
        assert ModelHTTPError in cycle.HALTING_ERRORS


# ── Scoring is not repeated for nothing ───────────────────────────


@pytest.mark.django_db
class TestScoringIsSkippedWhenNothingMoved:
    """Fitting the GP dominates the cost of using it (~1.1s at 300 labels, against
    a 5s cycle), and scoring the same pool with the same labels cannot promote
    anybody — so an unchanged campaign must not rebuild the model at all."""

    @pytest.fixture(autouse=True)
    def _clear(self):
        cycle._scored_at.clear()
        yield
        cycle._scored_at.clear()

    def _score_twice(self, campaign, between=None):
        _deal(campaign, DealState.QUALIFIED)
        with patch("openoutreach.core.ml.qualifier.qualifier_for",
                   return_value=object()) as build, \
                patch("openoutreach.core.pipeline.ready_pool.promote_to_ready",
                      return_value=0):
            cycle._score_qualified(campaign, cycle._one_model_per_action(campaign))
            if between:
                between(campaign)
            cycle._score_qualified(campaign, cycle._one_model_per_action(campaign))
        return build

    def test_an_unchanged_pool_is_not_rescored(self, campaign):
        assert self._score_twice(campaign).call_count == 1

    def test_a_new_lead_reopens_scoring(self, campaign):
        build = self._score_twice(
            campaign, between=lambda c: _deal(c, DealState.QUALIFIED))
        assert build.call_count == 2

    def test_a_new_verdict_reopens_scoring(self, campaign):
        """A label the GP has not seen changes what it would say."""
        build = self._score_twice(
            campaign, between=lambda c: _deal(c, DealState.FAILED))
        assert build.call_count == 2

    def test_an_empty_pool_never_builds_the_model(self, campaign):
        with patch("openoutreach.core.ml.qualifier.qualifier_for") as build:
            assert cycle._score_qualified(
                campaign, cycle._one_model_per_action(campaign)) is False
        build.assert_not_called()

    def test_one_action_fits_the_model_at_most_once(self, campaign):
        """Scoring and the top-up both need the GP, and the fit is what costs — so an
        action that falls through the promote gate into the top-up must not build a
        second model over the very same labels."""
        _deal(campaign, DealState.QUALIFIED)

        with patch("openoutreach.core.ml.qualifier.qualifier_for",
                   return_value=object()) as build, \
                patch("openoutreach.core.pipeline.ready_pool.promote_to_ready",
                      return_value=0), \
                patch("openoutreach.core.pipeline.top_up.top_up",
                      return_value=True) as fill:
            assert cycle.run_one_action(campaign) is True

        assert build.call_count == 1
        assert fill.call_args.args[1] is build.return_value


# ── An action is one action, and idleness is an answer ────────────


@pytest.mark.django_db
class TestOneActionAtATime:
    """What the bounded job depends on: `run_one_action` does at most one thing and
    reports honestly whether it did. `False` is the job's terminal condition, so a row
    that claims to have acted when it merely retried would be an endless run."""

    def test_it_stops_at_the_first_row_that_can_act(self, campaign, steps):
        _deal(campaign, DealState.FINDING_EMAIL, lookup_request_id="req1")
        _deal(campaign, DealState.QUALIFIED)

        assert cycle.run_one_action(campaign) is True
        assert _called(steps) == {"check"}

    def test_nothing_to_do_says_what_it_is_waiting_on(self, campaign, steps, caplog):
        """*Nothing may be reported as an empty result*: a job that stops short has to
        be able to say whether the index is drained or an address is on order.

        The line itself sits at DEBUG, because the operator meets the same summary once,
        as the job's `goal_unreached` detail — see `tests/test_job.py`. Printing it here
        too would read as two different findings.
        """
        _deal(campaign, DealState.FINDING_EMAIL, lookup_request_id="req1",
              not_before=timezone.now() + timedelta(hours=1))

        with caplog.at_level("DEBUG"):
            assert cycle.run_one_action(campaign) is False

        idle = [r.getMessage() for r in caplog.records if "nothing to do" in r.getMessage()]
        assert len(idle) == 1 and "address on order" in idle[0]

    def test_a_keyless_run_says_discovery_stopped_too_not_just_the_lookup(self, campaign):
        """One key does two jobs, so losing it has two consequences and both get named.

        Naming only the address half sent the operator after the wrong thing: without a
        key the walk finds nobody at all, which is the larger of the two.
        """
        with patch("openoutreach.enrichment.bettercontact.is_configured", return_value=False):
            summary = cycle.pipeline_summary(campaign)

        assert "no new discovery" in summary and "free address sources only" in summary

    def test_ranked_leads_and_no_emails_flag_names_that_gate(self, campaign):
        """The gate most likely to be holding on a bare `find` is the one it never
        mentioned: spending is opt-in, and an operator expecting addresses has to be
        told which flag turns it on."""
        _deal(campaign, DealState.READY_TO_FIND_EMAIL)

        with patch("openoutreach.enrichment.bettercontact.is_configured", return_value=True):
            summary = cycle.pipeline_summary(campaign, buy_addresses=False)

        assert "--emails" in summary
