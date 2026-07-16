# openoutreach/core/pipeline/icp.py
"""ICP seed generator — LLM maps a campaign to its first Lead Finder query.

A single LLM pass turns ``product_docs + campaign_target`` into candidate values
per family (titles, seniorities, countries, a size band), which are composed into
the **seed conjunction**: the campaign's first query. Called by the frontier's
bootstrap move on a cold start, and nowhere else.

This is the only unprompted LLM call in discovery, and it is unavoidable — with no
positives yet, the product description is the only prior available. Thereafter the
frontier steers on measured node counts and only asks the LLM again at a wall.

**A returned list per family is a set of candidates, not an OR.** A query holds at
most one clause per family: an include-list of 5 titles compresses 5 sampling
windows of ~10k rows into 1, and is strictly dominated by 5 separate queries, which
are free. Only the top value of each family reaches the seed; composing the rest
into further conjunctions needs the descent (next item on the roadmap card
``p2-e3-discovery-query-graph-search``), so until that lands the LLM re-proposes
them at a wall. See ``discovery.filters_for``.
"""
from __future__ import annotations

import logging

import jinja2
from pydantic import BaseModel, Field
from termcolor import colored

from openoutreach.core.conf import PROMPTS_DIR
from openoutreach.core.models import Clause
from openoutreach.discovery import LEAD_SENIORITIES, Seniority, describe_clauses

logger = logging.getLogger(__name__)


class ICPSpec(BaseModel):
    """The LLM's provider-agnostic ICP output — candidate values per family.

    ``seniorities`` is typed to Lead Finder's vocabulary, not ``list[str]``: an
    unknown level returns an empty page rather than an error, which the frontier
    would misread as the seed drying up. The schema makes that unrepresentable.
    The other families are free text — a value the index doesn't carry is a normal
    empty page, one move spent.
    """

    job_titles: list[str] = Field(default_factory=list)
    seniorities: list[Seniority] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    headcount_min: int = 1
    headcount_max: int = 10000
    country_code: str = ""


# The ICP's multi-value families — the ones a descent has something to vary. The
# headcount bounds are deliberately absent: each is a single number, so they ride
# every conjunction unchanged (see ``_pool_clauses``).
_VARYING_FAMILIES = (
    ("lead_job_title", "job_titles"),
    ("lead_seniority", "seniorities"),
    ("lead_location", "locations"),
)


def _seed_conjunction(spec: ICPSpec) -> list[tuple[str, str]]:
    """Compose the seed's clause set — the LLM's top pick in each family.

    Taking the first value per family takes the model's own ranking at face value,
    which is the only prior a cold start has. The remaining values are dropped
    rather than ORed in, because an OR would collapse their windows into one — they
    are kept as the campaign's *pool* instead (``_pool_clauses``), where the descent
    composes them into further conjunctions one at a time.
    """
    clauses = [
        ("company_headcount_min", str(spec.headcount_min)),
        ("company_headcount_max", str(spec.headcount_max)),
    ]
    for family, attr in _VARYING_FAMILIES:
        values = getattr(spec, attr)
        if values:
            clauses.append((family, values[0]))
    return sorted(clauses)


def _pool_clauses(spec: ICPSpec) -> list[tuple[str, str]]:
    """Every candidate clause the ICP produced — the campaign's clause pool.

    The seed is one point in the lattice this spans; the descent walks the rest
    without another LLM call. That is the whole point of keeping them: the model
    hands back 5 job titles, the seed can carry exactly one, and the other 4 used
    to be dropped on the floor and re-invented at the next wall.

    The headcount bounds are included with their single value each, so they appear
    in every conjunction the descent composes and never vary — a size band is this
    campaign's ICP, not a knob to search.
    """
    clauses = [
        ("company_headcount_min", str(spec.headcount_min)),
        ("company_headcount_max", str(spec.headcount_max)),
    ]
    for family, attr in _VARYING_FAMILIES:
        clauses.extend((family, value) for value in getattr(spec, attr))
    return sorted(set(clauses))


def generate_seed(campaign) -> list[tuple[str, str]]:
    """LLM-generate the campaign's seed query and fold its country onto it.

    The walk's cold start: with no nodes there is no line to page and nothing to
    deepen, so this is the one place a first query comes from. The seed isn't cached
    — its first fetched page becomes the node that carries its clauses thereafter.

    Also folds ``country_code`` onto the campaign, which is what geo-stamps every
    discovered Lead. Returns the seed's clause set, or ``[]`` when the ICP is empty.
    """
    from pydantic_ai import Agent

    from openoutreach.core.llm import get_llm_model, run_agent_sync

    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(PROMPTS_DIR)))
    prompt = env.get_template("icp_filters.j2").render(
        product_docs=campaign.product_docs,
        campaign_target=campaign.campaign_target,
        seniorities=LEAD_SENIORITIES,
    )

    agent = Agent(
        get_llm_model(),
        output_type=ICPSpec,
        model_settings={"temperature": 0.3, "timeout": 60},
    )
    spec = run_agent_sync(agent.run(prompt)).output

    clauses = _seed_conjunction(spec)
    if not clauses:
        return []

    pool = _pool_clauses(spec)
    campaign.clauses.set(Clause.rows_for(pool))

    country_code = spec.country_code.lower()
    if country_code and campaign.country_code != country_code:
        campaign.country_code = country_code
        campaign.save(update_fields=["country_code"])
    logger.info("[%s] %s: %s (pool: %d clause(s))", campaign,
                colored("discovery seed", "cyan", attrs=["bold"]),
                colored(describe_clauses(clauses), "cyan"), len(pool))
    return clauses
