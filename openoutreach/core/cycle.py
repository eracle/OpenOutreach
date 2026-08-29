# openoutreach/core/cycle.py
"""One action — do the single most valuable thing available for a campaign, and say so.

**This module no longer owns a loop.** It used to run a daemon: `run_daemon` rotated over
the operator's campaigns forever, sleeping `CYCLE_SECONDS` between actions. That process
never ended, so it could never return a result — which is why `status`, a `next_action`
object and a CSV on disk all had to exist for anyone to find out what it had done. The
loop now lives in `core/job.py`, is bounded by a goal, and ends when `run_one_action`
returns `False`. What is left here is the hierarchy itself, which never depended on the
loop's shape.

**A queue is a status, not a table.** Work is found by asking the deals what they
need (``Deal.objects.filter(state=...)``), so a deal is available because of its own
row. Nothing is created in advance, which means nothing can drift, be lost, or need
reconciling — and no row's timestamp can gate anything but itself.

What this replaces was a *token loop*. ``core/scheduler.py`` wrote ``Task`` rows that
were permission tokens stamped with a time — "at 14:32 someone may send one email for
campaign A" — without saying to whom; the loop took the earliest-due token, and the
handler then went and found its own target. When no token was due it slept **until
the earliest token's timestamp**. On 2026-08-05 that put a live install to sleep for
34 hours: two BetterContact polls had never terminated, their uncapped backoff had
reached 45h30m, they were the only rows in the table, and 55 ready deals with 70
sends of headroom simply had no token and therefore were not work. The shape was
inherited from the Playwright era, when a token queue was how access to one browser
got serialised. The browser went; the queue outlived it.

Here there is no sleep at all: a job asks for one action at a time and stops when there
is none, and every wait that matters is written on the row that is waiting.

## The hierarchy

One ordered list. The cycle walks it top to bottom and stops at the first thing it
can do, so priority is just the order these are written in:

    1  FINDING_EMAIL          check the lookup            (not_before elapsed)
    2  QUALIFIED              score with the campaign's model
    3  READY_TO_FIND_EMAIL    buy the address             (free sources first)
    4  (the campaign itself)  top up the pipeline         (always)

A state that is not listed is terminal, and terminal costs nothing: RESOLVED,
NO_EMAIL_FOUND and FAILED. Most deals come to rest at one of those three,
and resting is free because nothing iterates them.

**There is no spend gate on rows 2 and 4, and that is the shape of the finder.**
Both rows used to share one — *never resolve an address, and never spend an LLM call
qualifying, for someone there is no room to email today* — which was right while
every lead ended in a send. Nothing rations discovery or qualification now: they cost
the operator's own LLM key and nothing else, and the work is bounded the only way that
matters — one unit per call, and a goal that says how many calls. Row 3 is the single
paid step, and even there the payment is the last resort: an address in hand and the
hub cache are tried first and cost nothing, so the key and the credit are checked on
the paid leg alone rather than at the row. What caps the spend is the number the
operator typed, since one credit is one verified address.

**Two rows are gone with the sending leg** — answering a reply and sending a first
email — along with the mail pass and the daily warmth re-measure that fed them. They
live in OpenOutSend now. Leads leave this process over a CSV
(``core/export.py``) and nothing comes back up that wire.

**Campaigns no longer take turns.** ``_rotate`` round-robined them because a daemon had
to decide, forever, whose work to do next. A job names its campaign, so the fairness
question has nobody to be fair between and the scheduler that answered it is gone.
"""
from __future__ import annotations

import functools
import logging
import time

from django.utils import timezone
from pydantic_ai.exceptions import ModelHTTPError
from termcolor import colored

from openoutreach.crm.models import DealState

logger = logging.getLogger(__name__)

# Errors that end the job rather than being retried. A misconfigured LLM key is not a
# transient fault: every action would raise it, so retrying is a way of failing slowly.
# `core/job.py` turns this into one typed line and a non-zero exit.
HALTING_ERRORS = (ModelHTTPError,)

# Per-campaign `(qualified, past-the-gate)` counts as of the last scoring pass, so a
# pool that has not moved is not re-scored. Process-local by design: one process per
# job, and a second job simply scores once more than it had to.
_scored_at: dict[int, tuple[int, int]] = {}

# ── The loop ──────────────────────────────────────────────────────


def run_one_action(campaign, buy_addresses: bool = False, max_new_lookups: int | None = None) -> bool:
    """Do the highest-priority thing available for *campaign*. Returns whether it did.

    Each row is a query and a step. The first one that produces work wins and the
    cycle returns — nothing below it runs, which is what makes the order a priority.

    Every row is timed and named, because the hierarchy is also the only account of
    where the daemon's time goes: the steps log what they *did*, but a row that takes
    twenty seconds to decide it has nothing to do says so nowhere else.

    **``buy_addresses`` defaults to False, so spending is opt-in at every layer.** It
    enables the one paid row; without it no caller can spend a credit, however many
    deals have queued up past the confidence gate. The default used to be True, which
    made *not* spending the thing you had to remember to ask for — and a caller who
    forgets a flag should lose a feature, never money.

    **``max_new_lookups`` bounds how many *more* leads may be handed to ``buy_address``
    this call, independent of ``buy_addresses``.** Without it, a goal counted in
    ``emails`` could submit far more paid lookups than asked for: ``core/job.py``'s loop
    keeps calling this function until a lookup *resolves*, but a submission almost never
    resolves synchronously (the provider is async), so every call that doesn't resolve
    one just goes and submits a *different* lead's lookup instead — one credit and one
    profile handed to the resolver per call, with nothing stopping it at the count the
    operator typed. ``0`` (not ``None``) skips row 3 for this call the same way
    ``buy_addresses=False`` does, so a caller can throttle without disabling spending
    outright.

    **It is a cap on addresses *on order*, not on submissions made** —
    ``job._lookup_budget`` owns that arithmetic and its docstring says why. What matters
    here is that the number can go back **up**: a lookup that misses releases its slot,
    so a row skipped for "budget spent" on one call is live again on a later one, and
    ``may_spend`` must therefore be recomputed per call rather than latched.
    """
    if campaign is None:
        return False

    may_spend = buy_addresses and (max_new_lookups is None or max_new_lookups > 0)
    model = _one_model_per_action(campaign)
    for name, row, spends in ROWS:
        if spends and not may_spend:
            reason = "addresses not requested" if not buy_addresses \
                else "this run's lookup budget is spent"
            logger.debug("[%s] → %s? skipped — %s", campaign, name, reason)
            continue
        logger.debug("[%s] → %s?", campaign, name)
        started = time.monotonic()
        acted = row(campaign, model)
        elapsed = time.monotonic() - started
        if acted:
            # Which row of the hierarchy fired, and how long it took, is how *we* read a
            # run. The operator reads the action's own block header instead.
            logger.debug("[%s] %s — %.1fs", campaign,
                         colored(name, "cyan", attrs=["bold"]), elapsed)
            return True
        logger.debug("[%s] %s: nothing (%.1fs)", campaign, name, elapsed)
    # At DEBUG because it is not the last word: the job turns this same summary into its
    # `goal_unreached` line, and printing it twice would read as two different findings.
    logger.debug("[%s] nothing to do — %s", campaign,
                 pipeline_summary(campaign, buy_addresses=buy_addresses))
    return False


def _one_model_per_action(campaign):
    """The campaign's model, built on first use and shared by every row that scores.

    Rows 2 and 4 both score with the GP, and *building* it is the expensive part —
    the fit is O(n³) in the label count, seconds where the rest of the action is
    milliseconds. Each row used to call ``qualifier_for`` for itself, so the ordinary
    action — the promote gate holds, the walk falls through to the top-up — fitted the
    same labels twice, back to back, once per action forever.

    Sharing one is safe because nothing between the two rows can change what the model
    would say: row 4 only runs when row 2 promoted nobody, and a promotion moves a
    deal's *state*, not its fit verdict, which is what ``Lead.get_labeled_arrays``
    reads.

    What this saves now is the *lookup*, not the fit — ``qualifier_for`` keeps the fitted
    model under a fingerprint of the labels, so the second row would get the same object
    back anyway. It stays because one call is still cheaper than two, and because the
    two-rows-one-model invariant is worth stating where the rows are.
    """
    from openoutreach.core.ml.qualifier import qualifier_for

    return functools.cache(lambda: qualifier_for(campaign))


# What each waiting state means to someone reading a log, in pipeline order. The state
# names are the schema's vocabulary and belong in the schema — READY_TO_FIND_EMAIL says
# where a deal sits in a diagram, "waiting for an address to be bought" says what is
# actually happening to that person.
_WAITING_ON = (
    (DealState.QUALIFIED, "waiting to be ranked"),
    (DealState.READY_TO_FIND_EMAIL, "waiting for an address to be bought"),
    (DealState.FINDING_EMAIL, "address on order"),
    (DealState.RESOLVED, "resolved, ready to export"),
)


def pipeline_summary(campaign, buy_addresses: bool = True) -> str:
    """One line of counts: who is waiting on what, and which gate is holding them.

    Public because it is also the answer to *why did the job stop short* — a drained
    index and three addresses still on order are a dead end and a reason to run again in
    an hour, and "7 of 10" alone cannot tell them apart.

    ``buy_addresses`` is the run's own permission to spend, which the caller holds and
    this function cannot see. **A gate that is holding must be named**, and *the operator
    did not ask for addresses* is the one most likely to be holding on a bare `find`.
    """
    from django.db.models import Count

    from openoutreach.crm.models import Deal
    from openoutreach.enrichment import bettercontact, provider

    counts = dict(
        Deal.objects.filter(campaign=campaign, lead__disqualified=False)
        .values_list("state")
        .annotate(n=Count("state"))
        .values_list("state", "n"),
    )
    waiting = [f"{counts.get(state, 0)} {phrase}" for state, phrase in _WAITING_ON]

    # Each gate said as its consequence rather than as its name — a boolean tells you
    # nothing — and **every consequence it has**. The two keys are not symmetric: the
    # BetterContact key does two jobs (Lead Finder discovery *and* paid enrichment)
    # while an Apollo key does only the second, so an Apollo-only install discovers
    # nobody however healthy its enrichment is. Reporting that as one "no finder key"
    # sent the operator after the wrong key, so the two consequences are named apart.
    # What neither stops is the free address sources: an address already on the lead
    # and the hub's cache are both still read. Qualification has no gate — it always runs.
    ranked = counts.get(DealState.READY_TO_FIND_EMAIL, 0)
    can_discover = bettercontact.is_configured()
    can_resolve = provider.active() is not None

    if not can_discover and not can_resolve:
        held = " · no finder key, so no new discovery and free address sources only"
    elif not can_discover:
        held = " · no BetterContact key, so no new discovery — enrichment still runs"
    elif not buy_addresses and ranked:
        held = f" · {ranked} ranked but addresses not requested — add --emails to buy them"
    else:
        held = ""
    return f"{' · '.join(waiting)}{held}"


# ── 1. Check an in-flight lookup ──────────────────────────────────


def _check_lookups(campaign, model) -> bool:
    from openoutreach.enrichment.lookup import check_lookup, reclaim_lookup

    deal = _due(campaign, DealState.FINDING_EMAIL).first()
    if deal is None:
        return False
    # A deal here without a handle has no job to poll — reclaim it rather than skip
    # it, or it sits in a state no other row claims for as long as the install runs.
    step = check_lookup if deal.lookup_request_id else reclaim_lookup
    return _apply(deal, step(deal))


# ── 4. Score the qualified pool ───────────────────────────────────


def _score_qualified(campaign, model) -> bool:
    """Promote every QUALIFIED deal the campaign's model is confident about.

    The one step that is per-campaign rather than per-deal: building the model
    dominates the cost of using it, so once it is in hand it scores the whole pool
    in a single pass. It is *not* dropped afterwards — ``model`` is the action's
    shared one (see ``_one_model_per_action``), so the top-up row below reuses this
    same fit rather than paying for its own.

    Skipped entirely while nothing has changed since the last pass. Scoring is a
    pure function of two things — the labels the GP fits on and the pool it scores —
    so re-running it against the same counts cannot promote anybody, and a campaign
    whose pool sits below the gate would otherwise refit a GP every few seconds
    forever (measured: ~1.1s at 300 labels, against a 5s cycle). The counts are two
    indexed `COUNT`s, and being wrong costs one cycle's delay, never a wrong answer.
    """
    from openoutreach.core.pipeline.ready_pool import promote_to_ready

    before = _pool_signature(campaign)
    if before[0] == 0 or _scored_at.get(campaign.pk) == before:
        return False

    promoted = promote_to_ready(campaign, model())
    _scored_at[campaign.pk] = _pool_signature(campaign)
    return promoted > 0


def _pool_signature(campaign) -> tuple[int, int]:
    """`(deals awaiting the gate, deals already past it)` — what scoring depends on.

    The second count stands in for the label set: every state but QUALIFIED is a
    verdict the GP fits on, and the anchors thin out in step with acceptances, so
    any change to the model's inputs moves one of these two numbers.
    """
    from openoutreach.crm.models import Deal

    of_campaign = Deal.objects.filter(campaign=campaign)
    return (
        of_campaign.filter(state=DealState.QUALIFIED).count(),
        of_campaign.exclude(state=DealState.QUALIFIED).count(),
    )


# ── 5. Buy an address ─────────────────────────────────────────────


def _buy_addresses(campaign, model) -> bool:
    from openoutreach.enrichment.lookup import buy_address

    # **This row has no gate on it any more, and that is deliberate.** It used to
    # decline unless a provider key was configured, which was a gate on the *row*
    # standing in for a gate on the *spend* — and the row does more than spend. An
    # address already on the lead and the hub's cross-operator cache are both free,
    # both tried first, and a missing or exhausted key switched them off along with
    # the paid leg, exactly when a free hit was worth the most.
    #
    # ``buy_address`` now owns the key check on the paid leg alone (``is_configured``,
    # inside ``_submit``). Nothing checks a credit before spending one: an empty wallet
    # is a 402 from the provider, typed at the HTTP boundary. What bounds the spend is
    # the operator's own prepaid balance — see ``_top_up`` for why nothing here rations
    # it on our side.
    deal = _due(campaign, DealState.READY_TO_FIND_EMAIL).filter(
        lead__disqualified=False).first()
    if deal is None:
        return False
    return _apply(deal, buy_address(deal))


# ── 6. Top up the pipeline ────────────────────────────────────────


def _top_up(campaign, model) -> bool:
    """Discover and qualify, always. This row has no gate, and that is the pivot.

    It used to have two — a mailbox had to exist and the campaign had to have send
    headroom left today — because every lead ended in a send, so spending an LLM
    call on someone there was no room to email was waste. Rows 5 and 6 shared that
    one gate (``room_to_send_today``, now deleted): *never resolve an address, and
    never qualify a lead, for someone there is no room to email today.*

    **The product no longer sends, so neither clause survives.** The output is a
    qualified lead with a written reason, handed over as CSV; whether a mailbox
    exists says nothing about whether that lead is worth finding. Worse, the gate
    made a mailbox-less install produce *nothing* — with no ``Mailbox`` rows the
    pool headroom is 0, the comparison is never true, and discovery and
    qualification both stop. Not degraded: silent and empty.

    Nothing replaces it, because there is nothing left to ration. Discovery is free
    (``discovery.py``) and qualification costs one LLM call against a key the
    operator already pays for directly. A daily cap would be a knob invented to
    replace a gate that had a reason, and the loop is already rate-bounded the only
    way that matters: one unit of work per cycle, forever.
    """
    from openoutreach.core.pipeline.top_up import top_up

    return top_up(campaign, model())


# ── The hierarchy ─────────────────────────────────────────────────
#
# **The loop has no periodic side-effects any more.** It used to run two before every
# action: the mail pass (IMAP into each box, every five minutes) and the daily warmth
# re-measure (an IMAP round trip per box to re-derive its send cap). Both were about
# reading what the world did with our email, and neither has a caller here now — a
# finder emits no email to have an answer to. They moved with the sending leg.

# The ordered list from this module's docstring, made data so the walk can name the
# row it fired. Defined here rather than at the top because it holds the functions
# themselves — the order of this tuple *is* the priority, and there is nowhere else
# it is written down.
#
# The names are what the operator reads in the log, so they say what happens to a
# lead, not which function ran. "top up" named the function and explained nothing;
# "find & qualify new leads" is what that row actually does.
#
# The third element is whether the row spends money, which is what `--emails` turns
# *on*. Only row 3 carries it: checking a lookup we already submitted is free, and
# abandoning one would waste a credit already committed rather than save it.
#
# Every row is called as `row(campaign, model)` with the action's shared model getter,
# so the two rows that score share one fit; the rows that do not score take it and
# ignore it, which is cheaper than two call shapes for the walk to tell apart.
ROWS = (
    ("check for the email address we ordered", _check_lookups, False),
    ("rank the qualified leads", _score_qualified, False),
    ("buy an email address", _buy_addresses, True),
    ("find & qualify new leads", _top_up, False),
)


# ── Plumbing ──────────────────────────────────────────────────────


def _due(campaign, state):
    """This campaign's deals in *state* that are not waiting on a ``not_before``."""
    from django.db.models import Q

    from openoutreach.crm.models import Deal

    return (
        Deal.objects.filter(campaign=campaign, state=state)
        .filter(Q(not_before__isnull=True) | Q(not_before__lte=timezone.now()))
        .select_related("lead", "campaign")
        .order_by("creation_date")
    )


def _apply(deal, next_state) -> bool:
    """Persist whatever the step did to *deal*, including its new state.

    One save for the transition and the fields that justify it, so a deal can never
    be moved to RESOLVED without the address that justifies it, or parked at
    FINDING_EMAIL without the job handle to poll. A step that returns ``None`` has
    decided to stay put — it has usually written ``not_before`` — and that is saved
    just the same.
    """
    if next_state is not None:
        deal.state = next_state
    deal.save()
    return True


# ``_import_freemium_campaign`` ran here at every daemon start: fetch the published
# kit from HuggingFace, mirror it into a local campaign flagged ``is_freemium``, seed
# its leads. That campaign existed to send **OpenOutreach's own promotional email from
# the operator's mailbox, under their identity**, taking its turn in the rotation
# alongside the operator's real campaigns. It is gone, and so is everything that
# served it — the kit download, ``KitQualifier``, the freemium pool, the flag.
#
# It went for two reasons, and only the first is the obvious one. With no sending leg
# it could do nothing but mint QUALIFIED deals nobody would ever contact. But it was
# also **one of the largest install deterrents this project had**: the legal notice
# told every prospective operator, before they had seen a single lead, that the tool
# would send the maintainer's ads from their own mailbox. It was counted as the viral
# growth loop and was simultaneously suppressing the installs that loop needed.
#
# What replaces it is on this side of the boundary: install → enrichment resolves
# addresses → the hub contacts store grows → we market to the store. Removing the
# promo campaign raises the input to the loop that replaces it.
