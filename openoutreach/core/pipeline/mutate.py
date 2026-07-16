# openoutreach/core/pipeline/mutate.py
"""LLM-driven query mutation — propose one new Lead Finder filter set (MVP).

The frontier's breadth move: given a campaign's past query nodes and their
measured value, ask the LLM for a *single* new, distinct parameter set to try.
One query per call — the frontier widens a fetch at a time as its breadth floor
drains, and a single filter dict is what the model reliably produces. Kept behind
a small swappable interface (``set_generator``) so the learned cluster→query
replacement (roadmap Future work) can drop in without touching the frontier.

The proposal is a **typed** output (``_Filters``), not a free dict, because Lead
Finder answers an unknown key or value with an *empty page rather than an error* —
which the frontier reads as end-of-depth. Constraining the filter families and the
seniority vocabulary in the schema makes that class of silent kill unrepresentable;
the prompt then only has to carry strategy. What the schema still cannot catch is a
plausible-but-nonexistent *free-form* value (an invented technology or skill), which
is why the prompt insists on real-world values.

Non-scaling by construction: the prompt lists past queries, so history size is
the bound — fine for the MVP, and the reason the generator sits behind an
interface.
"""
from __future__ import annotations

import logging
from typing import Protocol

import jinja2
from pydantic import BaseModel, Field

from openoutreach.core.conf import PROMPTS_DIR
from openoutreach.discovery import Seniority

logger = logging.getLogger(__name__)

# Cap on past queries listed in the prompt — the crude bound on prompt growth.
PAST_QUERY_LIMIT = 40


class MutationGenerator(Protocol):
    """A callable proposing one new Lead Finder filter dict (or empty for none)."""

    def __call__(self, campaign) -> dict: ...


class _StringFilter(BaseModel):
    """An include-list over free-form strings (industry, location, tech, skills)."""

    include: list[str] = Field(min_length=1)


class _JobTitleFilter(BaseModel):
    """Job-title include-list; ``exact_match`` off keeps the match fuzzy."""

    include: list[str] = Field(min_length=1)
    exact_match: bool = False


class _SeniorityFilter(BaseModel):
    """Seniority include-list, constrained to Lead Finder's 12 levels."""

    include: list[Seniority] = Field(min_length=1)


class _Filters(BaseModel):
    """The Lead Finder filter families a mutation may vary.

    Typed rather than a bare dict on purpose: the **field names** are the contract,
    and an unknown *key* is answered with an empty page rather than an error — which
    the frontier would read as end-of-depth. Constraining the families in the schema
    makes that unrepresentable.

    Only ``lead_seniority`` is a closed vocabulary (its documented levels really do
    match, so it is a ``Literal``). Every other family is **free text handed to a
    search engine** — including ``lead_department`` and ``lead_function``, whose
    published "enum" is fiction: those snake_case values match nothing, while plain
    labels like ``Sales`` or ``Human Resources`` match fine. A value that matches
    nothing simply returns an empty page, exactly as an invented industry or skill
    would; the frontier records that query as dry and moves on, which is correct.
    So the prompt's job is to supply plausible real-world values, not to be
    exhaustive.
    """

    company_headcount_min: int | None = Field(
        None, description="Smallest company size to match, in employees.")
    company_headcount_max: int | None = Field(
        None, description="Largest company size to match, in employees.")
    lead_job_title: _JobTitleFilter | None = Field(
        None, description="Role titles the person actually holds, e.g. 'Head of Sales'. "
                          "Plain titles — no boolean syntax.")
    lead_seniority: _SeniorityFilter | None = Field(
        None, description="How senior the person is, from a fixed set of levels.")
    lead_industry: _StringFilter | None = Field(
        None, description="Industries the target companies operate in.")
    lead_location: _StringFilter | None = Field(
        None, description="Geographies to search — countries or regions, e.g. 'United States'.")
    company_technology: _StringFilter | None = Field(
        None, description="Technology the company uses, by product name, "
                          "e.g. 'salesforce', 'hubspot', 'shopify'.")
    lead_skills: _StringFilter | None = Field(
        None, description="Skills the person lists on their own profile, "
                          "e.g. 'negotiation', 'fundraising'.")
    lead_department: _StringFilter | None = Field(
        None, description="Department the person sits in, by its plain name, "
                          "e.g. 'Sales', 'Marketing', 'Human Resources'.")
    lead_function: _StringFilter | None = Field(
        None, description="Broad job function the person performs, by its plain name, "
                          "e.g. 'Sales', 'Operations', 'Legal'.")


class _FilterSet(BaseModel):
    """The LLM's proposed Lead Finder filter set."""

    filters: _Filters = Field(default_factory=_Filters)


def _past_query_stats(campaign) -> list[dict]:
    """Recent fetched nodes with their measured value, newest first.

    Every persisted node is a fetched query (the walk stores only fetched pages),
    so the LLM sees exactly what we have already tried and how it paid — its cue to
    propose something genuinely new.
    """
    from openoutreach.core.models import DiscoveryQuery

    nodes = (
        DiscoveryQuery.objects
        .filter(campaign=campaign)
        .order_by("-updated_at")[:PAST_QUERY_LIMIT]
    )
    return [
        {"params": n.params, "offset": n.offset, "score": n.score, "n_leads": n.leads.count()}
        for n in nodes
    ]


def llm_generate_mutation(campaign) -> dict:
    """Ask the LLM for one new distinct filter set from past-query stats."""
    from pydantic_ai import Agent

    from openoutreach.core.llm import get_llm_model, run_agent_sync

    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(PROMPTS_DIR)))
    prompt = env.get_template("mutate_queries.j2").render(
        product_docs=campaign.product_docs,
        campaign_target=campaign.campaign_target,
        past_queries=_past_query_stats(campaign),
    )
    agent = Agent(
        get_llm_model(),
        output_type=_FilterSet,
        model_settings={"temperature": 0.7, "timeout": 60},
    )
    filters = run_agent_sync(agent.run(prompt)).output.filters
    # Drop the families the model left unset — Lead Finder wants only the keys we
    # actually filter on, and an all-unset set degrades to {} (== "LLM is dry").
    return filters.model_dump(exclude_none=True)


_generator: MutationGenerator = llm_generate_mutation


def set_generator(generator: MutationGenerator) -> None:
    """Swap the active mutation generator (tests, or the future learned one)."""
    global _generator
    _generator = generator


def generate_mutation(campaign) -> dict:
    """Propose one new distinct filter set via the active generator.

    Mutation feeds discovery breadth but is not essential to a move — a failed or
    timed-out LLM call must not lose the fetch that already landed, so a failure
    here degrades to an empty dict (the node still deepens; the frontier just
    doesn't widen this move).
    """
    try:
        return _generator(campaign)
    except Exception:
        logger.exception("mutation generation failed — expanding without a new query")
        return {}
