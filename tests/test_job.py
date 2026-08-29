# tests/test_job.py
"""The bounded job: a goal, and the honest end of the work.

Three things carry the weight. **The goal is a delta** — "ten more than you had" is the
only reading under which running it twice gets you twenty. **Progress is a set, not a
subtraction**, so a lead the qualifier rejects mid-run cannot silently cancel out one that
was found. And **the job ends when nothing can advance**, which is the whole reason there
is no timeout: every wait that matters is already written on the row that is waiting.
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from openoutreach.core import job
from openoutreach.core.errors import ErrorType
from openoutreach.core.job import EMAILS, LEADS, Goal, run_job
from openoutreach.crm.models import DealState
from tests.factories import DealFactory, LeadFactory


def _exportable(campaign, email=None):
    """One lead the export would write — judged and accepted."""
    return DealFactory(campaign=campaign, lead=LeadFactory(email=email),
                       state=DealState.RESOLVED, reason="fits the ICP")


def _finds(campaign, per_action=1, email=None):
    """A `run_one_action` that produces leads, so a goal can actually be reached."""
    def action(_campaign, buy_addresses=True, max_new_lookups=None):
        for _ in range(per_action):
            _exportable(campaign, email=email)
        return True
    return patch("openoutreach.core.cycle.run_one_action", side_effect=action)


# ── reaching the goal ─────────────────────────────────────────────


@pytest.mark.django_db
class TestReachingTheGoal:
    def test_it_stops_as_soon_as_the_goal_is_met(self, campaign):
        with _finds(campaign) as action:
            result = run_job(campaign, Goal(3))

        assert result.reached and result.produced == 3
        assert action.call_count == 3

    def test_zero_does_no_work_and_is_already_met(self, campaign):
        """`find 0` is the "print what I have" case, and it falls out of the predicate
        rather than being a second verb."""
        with patch("openoutreach.core.cycle.run_one_action") as action:
            result = run_job(campaign, Goal(0))

        assert result.reached and result.produced == 0
        action.assert_not_called()

    def test_the_goal_is_a_delta_not_a_total(self, campaign):
        """Leads that were already there do not count toward the next ten."""
        _exportable(campaign)
        _exportable(campaign)

        with _finds(campaign) as action:
            result = run_job(campaign, Goal(2))

        assert result.produced == 2
        assert action.call_count == 2

    def test_a_rejection_cannot_cancel_out_a_find(self, campaign):
        """Progress is the set that *entered* the goal. Counting by subtraction would
        report zero here, having found a lead and lost an unrelated one."""
        doomed = _exportable(campaign)

        def find_one_lose_one(_campaign, buy_addresses=True, max_new_lookups=None):
            _exportable(campaign)
            doomed.state = DealState.FAILED
            doomed.outcome = "wrong_fit"
            doomed.save()
            return True

        with patch("openoutreach.core.cycle.run_one_action", side_effect=find_one_lose_one):
            result = run_job(campaign, Goal(1))

        assert result.reached and result.produced == 1


# ── the units are different sets ──────────────────────────────────


@pytest.mark.django_db
class TestUnits:
    def test_leads_counts_rows_without_an_address(self, campaign):
        """Exportable is not mailable: an address is an enrichment, never a
        precondition."""
        with _finds(campaign, email=None):
            result = run_job(campaign, Goal(2, LEADS))

        assert result.reached and result.produced == 2

    def test_emails_counts_only_the_rows_that_carry_one(self, campaign):
        """A lead already exportable that merely *gains* an address counts toward an
        `emails` goal — which is why progress is a set per unit and not a timestamp on
        the row."""
        deal = _exportable(campaign, email=None)

        def resolve(_campaign, buy_addresses=True, max_new_lookups=None):
            deal.lead.email = "ada@acme.com"
            deal.lead.save()
            return True

        with patch("openoutreach.core.cycle.run_one_action", side_effect=resolve):
            result = run_job(campaign, Goal(1, EMAILS))

        assert result.reached and result.produced_ids == [deal.lead.pk]

    def test_an_emails_goal_is_not_met_by_addressless_leads(self, campaign):
        with _finds(campaign, email=None):
            with patch("openoutreach.core.cycle.run_one_action") as action:
                action.side_effect = [True, False]
                result = run_job(campaign, Goal(1, EMAILS))

        assert not result.reached and result.produced == 0


# ── capping the addresses on order ───────────────────────────────


@pytest.mark.django_db
class TestEmailsGoalCapsAddressesOnOrder:
    """The budget is counted in **addresses**, not in submissions.

    A submission almost never resolves synchronously (the provider is async), so it never
    shows up in ``produced`` — but it has sent a profile to the resolver, and without a
    cap independent of ``produced`` a goal of 1 would submit a *different* lead's lookup
    on every call, since nothing else stops the loop before the goal-met check. So the
    run caps what is **on order at once**.

    What it must not cap is submissions *made*: a miss produced no address and must not
    spend the goal, or ``N emails`` tops out at the provider's hit rate."""

    def test_a_goal_of_one_submits_at_most_one_lookup(self, campaign):
        deals = [
            DealFactory(campaign=campaign, lead=LeadFactory(email=None),
                        state=DealState.QUALIFIED)
            for _ in range(3)
        ]
        submissions = []

        def fake_promote(_campaign, _qualifier):
            promoted = 0
            for deal in deals:
                deal.refresh_from_db()
                if deal.state == DealState.QUALIFIED:
                    deal.state = DealState.READY_TO_FIND_EMAIL
                    deal.save()
                    promoted += 1
            return promoted

        def fake_buy_address(deal):
            # Mirrors the real paid-submit path: parks at FINDING_EMAIL with a job
            # handle and a backoff far enough out that this run never re-polls it.
            submissions.append(deal.pk)
            deal.lookup_request_id = f"job-{deal.pk}"
            deal.not_before = timezone.now() + timedelta(hours=1)
            return DealState.FINDING_EMAIL

        with patch("openoutreach.core.ml.qualifier.qualifier_for", return_value=object()), \
             patch("openoutreach.core.pipeline.ready_pool.promote_to_ready",
                   side_effect=fake_promote), \
             patch("openoutreach.enrichment.lookup.buy_address",
                   side_effect=fake_buy_address), \
             patch("openoutreach.core.pipeline.top_up.top_up", return_value=False):
            result = run_job(campaign, Goal(1, EMAILS), buy_addresses=True)

        assert submissions == [deals[0].pk]
        assert not result.reached  # the one submission is still in flight, not resolved

    def test_a_miss_does_not_spend_the_goal(self, campaign):
        """``find 2 emails`` means two addresses, not two lookups.

        The URL-only query resolves ~42% of the time, so counting misses against the goal
        capped every ``emails`` run at the hit rate — ``find 400 emails`` could reach at
        most ~168 and then spun on discovery, unable to buy and unable to stop, until the
        operator interrupted it. A miss releases its slot; only a hit spends one."""
        deals = [
            DealFactory(campaign=campaign, lead=LeadFactory(email=None),
                        state=DealState.READY_TO_FIND_EMAIL)
            for _ in range(5)
        ]
        submissions = []

        def miss_twice_then_hit(deal):
            submissions.append(deal.pk)
            if len(submissions) <= 2:
                return DealState.NO_EMAIL_FOUND
            deal.lead.email = f"lead{deal.pk}@acme.com"
            deal.lead.save(update_fields=["email"])
            return DealState.RESOLVED

        with patch("openoutreach.core.ml.qualifier.qualifier_for", return_value=object()), \
             patch("openoutreach.enrichment.lookup.buy_address",
                   side_effect=miss_twice_then_hit), \
             patch("openoutreach.core.pipeline.top_up.top_up", return_value=False):
            result = run_job(campaign, Goal(2, EMAILS), buy_addresses=True)

        assert result.reached and result.produced == 2
        assert submissions == [deal.pk for deal in deals[:4]]  # two misses, then two hits


# ── stopping short ────────────────────────────────────────────────


@pytest.mark.django_db
class TestStoppingShort:
    def test_an_idle_cycle_ends_the_job_rather_than_spinning(self, campaign):
        """There is no timeout because there is nothing to time out: when no row can
        act, more waiting cannot change that — the waits live on the rows."""
        with patch("openoutreach.core.cycle.run_one_action", return_value=False):
            result = run_job(campaign, Goal(10))

        assert not result.reached
        assert result.stopped_because == ErrorType.GOAL_UNREACHED

    def test_the_reason_says_what_it_is_short_by_and_what_it_waits_on(self, campaign):
        """*Nothing may be reported as an empty result*: a drained index and three
        addresses on order are a dead end and a reason to run again in an hour."""
        DealFactory(campaign=campaign, lead=LeadFactory(), state=DealState.FINDING_EMAIL,
                    lookup_request_id="req1")

        with patch("openoutreach.core.cycle.run_one_action", return_value=False):
            result = run_job(campaign, Goal(10))

        assert "0 of 10 leads" in result.detail
        assert "address on order" in result.detail

    def test_work_done_before_stopping_is_still_reported(self, campaign):
        """Seven leads are seven leads, and the caller gets both the count and the rows."""
        acted = []

        def once(_campaign, buy_addresses=True, max_new_lookups=None):
            if acted:
                return False
            acted.append(True)
            _exportable(campaign)
            return True

        with patch("openoutreach.core.cycle.run_one_action", side_effect=once):
            result = run_job(campaign, Goal(10))

        assert not result.reached and result.produced == 1

    def test_a_halting_error_ends_the_job_with_an_answer(self, campaign):
        """A bad LLM key is not transient — every action would raise it, so retrying is
        a way of failing slowly."""
        from pydantic_ai.exceptions import ModelHTTPError

        with patch("openoutreach.core.cycle.run_one_action",
                   side_effect=ModelHTTPError(status_code=401, model_name="m", body=None)):
            result = run_job(campaign, Goal(10))

        assert result.stopped_because == ErrorType.BAD_CONFIG
        assert "llm_api_key" in result.detail

    def test_ctrl_c_hands_back_the_rows_not_a_stack_trace(self, campaign):
        """The operator's own deadline, for the one case with no natural bound: a
        campaign whose leads are all rejected keeps finding, keeps rejecting, and every
        row honestly reports that it acted."""
        def find_then_interrupt(_campaign, buy_addresses=True, max_new_lookups=None):
            if not campaign.deals.exists():
                _exportable(campaign)
                return True
            raise KeyboardInterrupt

        with patch("openoutreach.core.cycle.run_one_action", side_effect=find_then_interrupt):
            result = run_job(campaign, Goal(10))

        assert not result.reached and result.produced == 1
        assert "interrupted" in result.detail


# ── watching leads land ───────────────────────────────────────────


@pytest.mark.django_db
def test_each_new_lead_is_announced_once_as_it_lands(campaign):
    """What `--open` rides on: a profile goes in front of the operator while the job is
    still running, not in a burst at the end, and never twice."""
    seen = []

    with _finds(campaign):
        result = run_job(campaign, Goal(3), on_new_lead=seen.append)

    assert [lead.pk for lead in seen] == result.produced_ids
    assert len(set(lead.pk for lead in seen)) == 3


@pytest.mark.django_db
def test_a_lead_already_there_is_never_announced(campaign):
    """It is not news, and opening a tab for it would be a lie about what just happened."""
    old = _exportable(campaign)
    seen = []

    with _finds(campaign):
        run_job(campaign, Goal(1), on_new_lead=seen.append)

    assert old.lead.pk not in [lead.pk for lead in seen]


# ── what the operator is told while it works ──────────────────────


@pytest.mark.django_db
class TestTheProgressNarrative:
    """The number the operator typed is the denominator.

    Reporting through the state machine's names made them do the arithmetic in a
    vocabulary that is ours, not theirs.
    """

    def test_each_lead_reports_distance_to_the_goal(self, campaign, caplog):
        with _finds(campaign), caplog.at_level("INFO"):
            run_job(campaign, Goal(2))

        assert "1 of 2 leads" in caplog.text and "2 of 2 leads" in caplog.text

    def test_the_first_one_gets_its_own_milestone(self, campaign, caplog):
        """*How long until anything at all happens* is what a first run is really
        asking, and it is the number the whole first-run design is judged on."""
        with _finds(campaign), caplog.at_level("INFO"):
            run_job(campaign, Goal(2))

        milestones = [r.getMessage() for r in caplog.records if "first lead" in r.getMessage()]
        assert len(milestones) == 1

    def test_the_unit_the_operator_typed_is_the_one_reported(self, campaign, caplog):
        with _finds(campaign, email="ada@acme.com"), caplog.at_level("INFO"):
            run_job(campaign, Goal(1, EMAILS))

        assert "1 of 1 emails" in caplog.text and "first email" in caplog.text

    def test_the_result_carries_how_long_it_took(self, campaign):
        """Reported, never enforced — there is still no timeout."""
        with _finds(campaign):
            result = run_job(campaign, Goal(1))

        assert result.elapsed > 0


@pytest.mark.django_db
def test_the_unit_helper_reads_the_export_not_a_state(campaign):
    """`status` and a goal must agree on what "ten leads" means, so both count the rows
    the export would actually write."""
    _exportable(campaign, email="ada@acme.com")
    DealFactory(campaign=campaign, lead=LeadFactory(email="no@acme.com"),
                state=DealState.FAILED, outcome="wrong_fit", reason="no fit")

    assert len(job._unit_ids(campaign, LEADS)) == 1
    assert len(job._unit_ids(campaign, EMAILS)) == 1
