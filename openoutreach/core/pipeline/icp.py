# openoutreach/core/pipeline/icp.py
"""ICP seed generator — LLM maps a campaign to its first Lead Finder query.

A single LLM pass turns ``product_docs + campaign_target`` into **one value per
family** (a title, a seniority, a country, a size band): the single most precise
query the model can name. That conjunction is both the seed and the campaign's whole
**clause pool** — with one clause per family there is no held-back candidate for a
pool to keep, so the descent downstream can only *broaden* the seed (drop a clause),
never OR alternatives back in. Called by the frontier on a cold start, and nowhere
else.

The seed conjunction it returns is the head of level N in the lattice visit: the
walk opens on the ICP's strongest, deepest guess and widens toward the head only
when nothing there qualifies.

This is the only unprompted LLM call in discovery, and it is unavoidable — with no
positives yet, the product description is the only prior available. Thereafter the
walk composes its queries from this pool, and asks the LLM again only once the pool
spans nothing unvisited — which, with one value per family, comes quickly.

**One value per family, never a list.** A query holds at most one clause per family:
an include-list of 5 titles compresses 5 sampling windows of ~10k rows into 1, and
is strictly dominated by 5 separate queries, which are free. The schema makes more
than one unrepresentable, so precision is enforced, not merely requested. See
``discovery.filters_for``.
"""
from __future__ import annotations

import logging

import jinja2
from pydantic import BaseModel
from termcolor import colored

from openoutreach.core.conf import PROMPTS_DIR
from openoutreach.core.models import Clause
from openoutreach.discovery import LEAD_SENIORITIES, Seniority, describe_clauses

logger = logging.getLogger(__name__)


class ICPSpec(BaseModel):
    """The LLM's provider-agnostic ICP output — one value per family.

    ``seniority`` is typed to Lead Finder's vocabulary, not ``str``: an unknown level
    returns an empty page rather than an error, which the frontier would misread as
    the seed drying up. The schema makes that unrepresentable. The other families are
    free text — a value the index doesn't carry is a normal empty page, one move
    spent. Each family is a single scalar, not a list: a query is one conjunction and
    an include-list would be an OR, which is strictly dominated (see the module
    docstring).
    """

    job_title: str = ""
    seniority: Seniority | None = None
    location: str = ""
    headcount_min: int = 1
    headcount_max: int = 10000
    country_code: str = ""


# The ICP's free-value families, paired with the ``ICPSpec`` attr each reads. The
# headcount bounds are deliberately absent: each is a single number that rides every
# conjunction unchanged, not a value the seed reads from a scalar field.
_CLAUSE_FAMILIES = (
    ("lead_job_title", "job_title"),
    ("lead_seniority", "seniority"),
    ("lead_location", "location"),
)


def _seed_conjunction(spec: ICPSpec) -> list[tuple[str, str]]:
    """Compose the seed clause set — one clause per family the ICP named.

    This is both the seed query and the entire clause pool: with one value per family
    there is no held-back candidate to keep, so the descent downstream can only
    broaden this conjunction (drop a clause), never OR alternatives in. A family the
    model left empty contributes no clause.

    The headcount bounds are always present with their single value each, so they
    appear in every conjunction the descent composes and never vary — a size band is
    this campaign's ICP, not a knob to search.
    """
    clauses = [
        ("company_headcount_min", str(spec.headcount_min)),
        ("company_headcount_max", str(spec.headcount_max)),
    ]
    for family, attr in _CLAUSE_FAMILIES:
        value = getattr(spec, attr)
        if value:
            clauses.append((family, value))
    return sorted(clauses)


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

    campaign.clauses.set(Clause.rows_for(clauses))

    country_code = spec.country_code.lower()
    if country_code and campaign.country_code != country_code:
        campaign.country_code = country_code
        campaign.save(update_fields=["country_code"])
    logger.info("[%s] %s: %s", campaign,
                colored("discovery seed", "cyan", attrs=["bold"]),
                colored(describe_clauses(clauses), "cyan"))
    return clauses
