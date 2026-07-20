# openoutreach/core/pipeline/mutate.py
"""The frontier's breadth move — one new Lead Finder query, composed or invented.

``descend_or_refill`` is what the wall actually calls, and the escalation is the
whole design: compose the next untried conjunction from the campaign's clause pool
(``descend.py``), and ask the LLM **only** when the pool spans nothing new. The
descent is free and cannot invent a value the index has never heard of; the LLM
call below is the refill for when a pool is genuinely used up.

Both sit behind ``set_generator``, so the frontier calls one function and never
learns which answered — the seam the learned cluster→query replacement (roadmap
Future work) drops into as well.

What follows is the **refill**: given a campaign's past query nodes and their
measured value, ask the LLM for a single new, distinct parameter set. It used to
run at *every* wall — 7 mutations in a few minutes on the observed run, 6 of them
returning 0 rows — because the clauses the ICP had already produced were being
thrown away instead of pooled.

The proposal is a **typed** output (``_Filters``), not a free dict, and the schema
carries **only families proven to steer** — each one verified live against an
unfiltered baseline plus an absurd-value control (2026-07-16).

An unknown key is **silently dropped**, and the query answers as if it were never
sent: you get the unfiltered page back, with rows. That is *not* an empty page, so
it never reads as end-of-depth — it reads as success, which is worse. An inert
family is an identity element: it lets the model believe it is steering while
producing a node indistinguishable from its parent. ``lead_industry``,
``company_technology`` and ``lead_skills`` were exactly that and are gone.

A *value* the index doesn't carry is different and benign: it returns an empty page,
costs one move, and the frontier records the clause as dry. Only ``lead_seniority``
is a closed vocabulary (a ``Literal``); the rest are search terms, so the prompt —
not the schema — carries the "real-world values only" rule.

Non-scaling by construction: the prompt lists past queries, so history size is
the bound — fine for the MVP, and the reason the generator sits behind an
interface.
"""
from __future__ import annotations

import logging
from typing import Protocol

import jinja2
from pydantic import BaseModel, ConfigDict, Field
from termcolor import colored

from openoutreach.core.conf import PROMPTS_DIR
from openoutreach.discovery import Seniority, describe_clauses, describe_filters

logger = logging.getLogger(__name__)

# Cap on past queries listed in the prompt — the crude bound on prompt growth.
PAST_QUERY_LIMIT = 40


class MutationGenerator(Protocol):
    """A callable proposing one new clause set (or empty for none).

    Clauses are ``(family, value)`` pairs — at most one per family, all ANDed. See
    ``discovery.filters_for``.
    """

    def __call__(self, campaign) -> list[tuple[str, str]]: ...


class _Filters(BaseModel):
    """The Lead Finder filter families a mutation may vary.

    Every family here is **verified to steer**: probed live against an unfiltered
    baseline with an absurd-value control, and kept only if the real value changed
    the returned page *and* the absurd value emptied it. Families that failed that
    test (``lead_industry``, ``company_technology``, ``lead_skills``) are absent by
    design — see the module docstring. "It returned rows" is not evidence.

    Only ``lead_seniority`` is a closed vocabulary (its documented levels really do
    match, so it is a ``Literal``). ``lead_department`` and ``lead_function`` are
    free text despite a published "enum" that is fiction — the snake_case values
    match nothing, while plain labels (``Sales``, ``Marketing``) match fine. A value
    the index doesn't carry returns an empty page; the frontier records that clause
    as dry and moves on, which is correct. So the prompt supplies plausible
    real-world values; the schema doesn't police them.

    ``extra="forbid"`` is load-bearing, not hygiene. Pydantic's default is to
    *ignore* an unknown key, which would reproduce the exact provider bug this
    schema exists to prevent: the model emits ``lead_industry``, it vanishes without
    a word, and the mutation is silently the same query as its parent. Forbidding
    puts ``additionalProperties: false`` in the JSON schema the LLM is generating
    against, and turns a violation into a retry instead of a no-op.

    **One value per family, not a list.** A query is a conjunction of single clauses:
    an include-list is an OR, and an OR is strictly dominated — it packs several
    ~10k-row windows into one, where separate queries would get a window each, for
    free. The schema is what makes that unrepresentable, so the model cannot spend a
    move collapsing five regions into one.
    """

    model_config = ConfigDict(extra="forbid")

    company_headcount_min: int | None = Field(
        None, description="Smallest company size to match, in employees.")
    company_headcount_max: int | None = Field(
        None, description="Largest company size to match, in employees.")
    lead_job_title: str | None = Field(
        None, description="One role title the person actually holds, e.g. 'Head of Sales'. "
                          "A plain title — no boolean syntax, no lists.")
    lead_seniority: Seniority | None = Field(
        None, description="How senior the person is — one level from a fixed set.")
    lead_location: str | None = Field(
        None, description="One country to search, by real country name, e.g. 'United States'. "
                          "Regions ('Europe', 'APAC') are not countries and match zero leads.")
    lead_department: str | None = Field(
        None, description="One department the person sits in, by its plain name, "
                          "e.g. 'Sales', 'Marketing', 'Human Resources'.")
    lead_function: str | None = Field(
        None, description="One broad job function the person performs, by its plain name, "
                          "e.g. 'Sales', 'Operations', 'Legal'.")


class _FilterSet(BaseModel):
    """The LLM's proposed Lead Finder filter set."""

    filters: _Filters = Field(default_factory=_Filters)


def _past_query_stats(campaign) -> list[dict]:
    """Recent fetched nodes with their measured value, newest first.

    Every persisted node is a fetched query (the walk stores only fetched pages),
    so the LLM sees exactly what we have already tried and how it paid — its cue to
    propose something genuinely new.

    Each row carries **three counts, never collapsed into one score**, because their
    zeros mean different things and only one of them is evidence against a region:
    ``n_leads = 0`` is a query the index has nothing for; ``qualified = 0`` over
    ``examined > 0`` is a real region full of the wrong people; ``examined = 0`` is
    nobody having looked yet, which licenses no conclusion at all. Collapsing them is
    how a region gets written off for having a bad *view* rather than being empty.
    """
    from openoutreach.core.models import DiscoveryQuery
    from openoutreach.core.pipeline.frontier import NodeStats, node_stats

    nodes = (
        DiscoveryQuery.objects
        .filter(campaign=campaign)
        .order_by("-updated_at")[:PAST_QUERY_LIMIT]
    )
    stats = node_stats(campaign)
    return [
        {
            "query": describe_filters(n.to_filters()),
            "offset": n.offset,
            "n_leads": n.leads.count(),
            "examined": stats.get(n.pk, NodeStats(0, 0)).examined,
            "qualified": stats.get(n.pk, NodeStats(0, 0)).qualified,
        }
        for n in nodes
    ]


def llm_generate_mutation(campaign) -> list[tuple[str, str]]:
    """Ask the LLM for one new distinct clause set from past-query stats."""
    from pydantic_ai import Agent

    from openoutreach.core.llm import get_llm_model, run_agent_sync

    past = _past_query_stats(campaign)
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(PROMPTS_DIR)))
    prompt = env.get_template("mutate_queries.j2").render(
        product_docs=campaign.product_docs,
        campaign_target=campaign.campaign_target,
        past_queries=past,
    )
    agent = Agent(
        get_llm_model(),
        output_type=_FilterSet,
        model_settings={"temperature": 0.7, "timeout": 60},
    )
    filters = run_agent_sync(agent.run(prompt)).output.filters
    # Drop the families the model left unset — Lead Finder wants only the keys we
    # actually filter on, and an all-unset set degrades to [] (== "LLM is dry").
    proposal = sorted(
        (family, str(value))
        for family, value in filters.model_dump(exclude_none=True).items()
    )
    # Logged like the seed it widens from: the proposal is the only record of what
    # the LLM invented, and a value it made up reads as an empty page downstream.
    logger.info("[%s] %s from %d past quer(ies): %s",
                campaign, colored("discovery mutation", "yellow", attrs=["bold"]),
                len(past), colored(describe_clauses(proposal), "cyan"))
    return proposal


def descend_or_refill(campaign) -> list[tuple[str, str]]:
    """Compose the next query from the clause pool; ask the LLM only if it can't.

    The escalation, and the order is the point::

        next unvisited conjunction → (only when none remain) → LLM refill

    The descent is a lattice lookup over clauses the LLM already produced, so it is
    free and it cannot invent a value the index has never heard of. Only when every
    conjunction the pool spans is fetched or pruned is there nothing left to compose,
    and only then is the LLM asked — which is a real answer ("this pool is used up"),
    not a fallback for an error.

    The seed carries one value per family, so the pool is the seed itself and the
    descent only broadens it — a ~5-clause seed spans just its subset lattice (a
    handful of conjunctions), so that condition arrives quickly and the LLM is asked
    again not long after cold start. The refill still invents a whole query today;
    making it mint *clauses* from returned rows is the next item on the card.
    """
    from openoutreach.core.pipeline.descend import descend

    return descend(campaign) or llm_generate_mutation(campaign)


_generator: MutationGenerator = descend_or_refill


def set_generator(generator: MutationGenerator) -> None:
    """Swap the active mutation generator (tests, or the future learned one)."""
    global _generator
    _generator = generator


def generate_mutation(campaign) -> list[tuple[str, str]]:
    """Propose one new distinct clause set via the active generator.

    Mutation feeds discovery breadth but is not essential to a move — a failed or
    timed-out LLM call must not lose the fetch that already landed, so a failure
    here degrades to an empty list (the node still deepens; the frontier just
    doesn't widen this move).
    """
    try:
        return _generator(campaign)
    except Exception:
        logger.exception("mutation generation failed — expanding without a new query")
        return []
