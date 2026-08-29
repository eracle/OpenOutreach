# openoutreach/core/job.py
"""A bounded run: work until the goal is met, or until nothing can advance.

The daemon this replaces never ended, which is the wrong shape for the reader we care
about most. An agent invoking a tool gets stdout, stderr and an exit code; a process that
does not finish gives it none of the three, so everything it wanted to know had to be
polled out of a second verb or watched in a file. A job with a goal returns all three.

**There is no timeout here, deliberately.** Every unit of work already carries its own
bound — ``deal.not_before`` is the architecture's one retry mechanism, the lookup poll
doubles its own backoff, and ``urllib3.Retry`` bounds the 429s. A job-level clock would be
a second timeout answering a question the first one already answers, and answering it
worse: a clock knows nothing about *why* it is waiting. So the terminal condition is
derived from real state instead — **the job ends when nothing can advance right now**,
which is exactly what ``cycle.run_one_action`` returning ``False`` already means.

**Progress is a set, not a subtraction.** A goal counts the leads that *entered* it during
this run, so a lead the qualifier rejects mid-run cannot silently cancel out one that was
found. It also makes ``--new`` exact for both units: for ``emails`` the rows that changed
are usually leads that were already exportable and merely got an address, which no
timestamp on the row would identify.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from termcolor import colored

from openoutreach.core.errors import ErrorType

logger = logging.getLogger(__name__)

LEADS = "leads"
EMAILS = "emails"
UNITS = (LEADS, EMAILS)


@dataclass(frozen=True)
class Goal:
    """How much more of what. ``count`` is a **delta**, not a total.

    *Ten more than you had when I started* is what "next 10" means in English, and the
    only reading under which running it twice gets you twenty.
    """

    count: int
    unit: str = LEADS

    def __str__(self) -> str:
        return f"{self.count} {self.unit}"


@dataclass
class JobResult:
    """What the job did — the one thing the exit code and ``--json`` are both built from."""

    goal: Goal
    produced_ids: list[int] = field(default_factory=list)
    stopped_because: str | None = None
    """``None`` when the goal was met; otherwise a type from ``core/errors.py``."""

    detail: str = ""

    elapsed: float = 0.0
    """Wall-clock seconds the job ran. Reported, not enforced — there is no timeout."""

    @property
    def produced(self) -> int:
        return len(self.produced_ids)

    @property
    def reached(self) -> bool:
        return self.stopped_because is None


def run_job(campaign, goal: Goal, on_new_lead=None, buy_addresses: bool = False) -> JobResult:
    """Work the cycle until *goal* is met or nothing can advance.

    Never raises on a provider refusal — it stops and reports which one. ``on_new_lead``
    is called with each lead as it enters the goal, which is how ``--open`` puts a profile
    in front of the operator while the job runs rather than in a burst at the end.

    **The unit caps the goal; ``buy_addresses`` is what permits the spend**, and it is
    off unless asked for. A goal counted in ``emails`` still needs it — the unit says
    what to count, never what may be paid for, and keeping those separate is what stops
    a run counting *leads* from quietly buying an address for whatever cleared the
    confidence gate on an earlier pass. That is what it used to do.

    The clock it keeps is **reported, never enforced** — see the module docstring for why
    there is no timeout. It exists so milestones can carry elapsed time, which is also how
    the first-run number stays measured rather than measured once.
    """
    started = time.monotonic()
    result = _work_to_goal(campaign, goal, on_new_lead, buy_addresses, started)
    result.elapsed = time.monotonic() - started
    return result


def _work_to_goal(campaign, goal: Goal, on_new_lead, buy_addresses: bool,
                  started: float) -> JobResult:
    """The loop itself. Every exit is a ``JobResult``; none of them raises."""
    from openoutreach.core.cycle import HALTING_ERRORS, run_one_action
    from openoutreach.enrichment.bettercontact import BetterContactUnavailable

    baseline = _unit_ids(campaign, goal.unit)
    result = JobResult(goal=goal)

    while result.produced < goal.count:
        try:
            acted = run_one_action(
                campaign, buy_addresses=buy_addresses,
                max_new_lookups=_lookup_budget(campaign, goal, result))
            _collect(campaign, goal, baseline, result, on_new_lead, started)
        except HALTING_ERRORS as exc:
            # A bad LLM key is not a transient fault: every action would raise it. The
            # daemon stopped the loop loudly for this; a job ends with an answer.
            result.stopped_because = ErrorType.BAD_CONFIG
            result.detail = (
                f"the model rejected the request ({exc}) — check ai_model, llm_api_key "
                "and llm_api_base"
            )
            return result
        except BetterContactUnavailable as exc:
            # A refusal the provider will repeat — a rejected key, an empty wallet, a
            # 429 that outlasted its backoff. Discovery raises these rather than
            # returning an empty page, so the run ends naming the refusal instead of
            # reporting the leads it did not find. The rows already produced stand.
            result.stopped_because = exc.error_type
            result.detail = f"{result.produced} of {goal} — {exc}"
            return result
        except KeyboardInterrupt:
            # The operator's own deadline. The one case with no natural bound is a
            # campaign whose leads are all rejected — the walk keeps finding, the
            # qualifier keeps saying no, and every row honestly reports that it acted.
            # An interrupt should hand back the rows, not a stack trace.
            result.stopped_because = ErrorType.GOAL_UNREACHED
            result.detail = f"interrupted — {result.produced} of {goal}"
            return result

        if not acted and result.produced < goal.count:
            result.stopped_because = ErrorType.GOAL_UNREACHED
            result.detail = _why_idle(campaign, result, buy_addresses)
            return result

    return result


# ── measuring the goal ───────────────────────────────────────────

def _unit_ids(campaign, unit: str) -> set[int]:
    """The leads that currently count toward *unit*.

    ``leads`` is everything the export would write; ``emails`` narrows that to the rows
    carrying an address. **Exportable is not mailable** — a `QUALIFIED` lead exports with
    a blank ``email``, because an address is an enrichment on top and never a
    precondition — so the two units are genuinely different sets, not a filter applied to
    one.
    """
    from openoutreach.core.export import lead_records

    return {
        record["lead_id"]
        for record in lead_records(campaign)
        if unit != EMAILS or record["email"]
    }


def _on_order(campaign) -> int:
    """How many addresses this campaign is waiting on — submitted, not yet hit or miss.

    Campaign-wide rather than run-scoped, because a lookup already in flight when the
    job started will count toward the goal the moment it lands (``_unit_ids`` reads the
    address, not who ordered it), so it has to count against the budget too.
    """
    from openoutreach.crm.models import Deal, DealState

    return Deal.objects.filter(campaign=campaign, state=DealState.FINDING_EMAIL).count()


def _lookup_budget(campaign, goal: Goal, result: JobResult) -> int | None:
    """How many *more* paid lookups this run may still submit, or ``None`` (no cap).

    **The budget is counted in addresses, not in submissions**, because that is the unit
    the operator typed. A lookup that comes back a miss produced no address, so it must
    not spend the goal — the URL-only query resolves ~42% of the time, so capping
    *submissions* at ``goal.count`` put a hard ceiling of ~``0.42 × N`` on every
    ``emails`` goal. ``find 400 emails`` could not reach 400 at any runtime, and once the
    last submission was spent the paid row was skipped forever while discovery kept
    acting, so the loop could not end either. It ran until the operator interrupted it.

    What is capped instead is how many addresses may be **on order at once**: the goal,
    less what has resolved, less what is still in flight. A miss releases its slot and the
    run buys again; a hit spends one for good. So at most ``goal.count`` addresses ever
    resolve, the spend is still capped at the number typed — one credit per verified hit —
    and a goal of 1 still submits exactly one lookup at a time rather than one for every
    lead that clears the confidence gate.

    Only an ``emails`` goal has a budget at all — ``leads`` isn't counted in paid
    lookups, so nothing here should throttle it.
    """
    if goal.unit != EMAILS:
        return None
    return max(0, goal.count - result.produced - _on_order(campaign))


def _collect(campaign, goal: Goal, baseline: set[int], result: JobResult, on_new_lead,
             started: float) -> None:
    """Record whatever entered the goal since the job started, once each, and say so."""
    from openoutreach.crm.models import Lead

    fresh = _unit_ids(campaign, goal.unit) - baseline - set(result.produced_ids)
    if not fresh:
        return

    for lead in Lead.objects.filter(pk__in=fresh):
        result.produced_ids.append(lead.pk)
        if on_new_lead is not None:
            on_new_lead(lead)
        _log_progress(campaign, goal, result, time.monotonic() - started)


def _log_progress(campaign, goal: Goal, result: JobResult, elapsed: float) -> None:
    """Distance to the goal, in the unit the operator typed.

    **The number they typed is the denominator.** Reporting state-machine names instead
    made the operator do the arithmetic in a vocabulary that is ours, not theirs. The
    first one gets its own milestone, because *how long until anything at all happens* is
    the question a first run is really asking.
    """
    from openoutreach.core.logging import format_elapsed
    from openoutreach.crm.models import Deal

    stamp = format_elapsed(elapsed)
    if result.produced == 1:
        logger.info("%s", colored(f"★ first {goal.unit[:-1]} · {stamp}", "green", attrs=["bold"]))

    seen = Deal.objects.filter(campaign=campaign).count()
    logger.info("  %s  %d of %d %s · %d seen · %s",
                colored("·", "cyan", attrs=["bold"]),
                result.produced, goal.count, goal.unit, seen, stamp)


def _why_idle(campaign, result: JobResult, buy_addresses: bool) -> str:
    """What the job is short by, and what it is waiting on, in one line.

    *Nothing may be reported as an empty result*: "7 of 10" alone leaves the reader unable
    to tell a drained index from three addresses still on order, which are a dead end and
    a reason to run again in an hour.
    """
    from openoutreach.core.cycle import pipeline_summary

    return (
        f"{result.produced} of {result.goal} — nothing left to do right now. "
        f"{pipeline_summary(campaign, buy_addresses=buy_addresses)}"
    )
