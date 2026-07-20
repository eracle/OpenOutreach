# openoutreach/core/pipeline/icp.py
"""ICP seed generator — LLM maps a campaign to its first, most-precise query.

A single LLM pass turns ``product_docs + campaign_target`` into **one value per
family** (a title, a seniority, a country, a size band): the single most precise
conjunction the model can name. That conjunction is the campaign's whole starting
**pool**, so the initial maximal set is exactly one query — the seed. Breadth is not
seeded; it grows from the leads that qualify (``mint.py``), which add more values per
family and so more maximals for the selector to rank.

This is the only unprompted LLM call in discovery, and it is unavoidable — with no
positives yet, the product description is the only prior available. Thereafter the
LLM is asked only to widen the axes from qualified profiles, never to compose a query.

One value per family, never headcount as a range to search: the size band is a single
ICP attribute that rides every maximal unchanged. See ``discovery.filters_for``.
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
    returns an empty page rather than an error, wasting a fetch. The other families
    are free text — a value the index doesn't carry is a normal empty page, one fetch
    spent. Each family is a single scalar: the seed is one precise conjunction, and
    minting — not the seed — supplies the alternatives.
    """

    job_title: str = ""
    seniority: Seniority | None = None
    location: str = ""
    headcount_min: int = 1
    headcount_max: int = 10000
    country_code: str = ""


# The ICP's free-value families, paired with the ``ICPSpec`` attr each reads. The
# headcount bounds are absent: each is a single number riding every maximal, not a
# value the seed reads from a scalar field.
_CLAUSE_FAMILIES = (
    ("lead_job_title", "job_title"),
    ("lead_seniority", "seniority"),
    ("lead_location", "location"),
)


def _seed_conjunction(spec: ICPSpec) -> list[tuple[str, str]]:
    """Compose the seed clause set — one clause per family the ICP named.

    Both the seed query and the whole starting pool: with one value per family the
    initial maximal set is this single conjunction. A family the model left empty
    contributes no clause. The headcount bounds are always present and appear in every
    maximal unchanged — a size band is this campaign's ICP, not a knob to search.
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

    The cold start: with no clauses there is nothing to fetch, so this is where the
    pool comes from. Also folds ``country_code`` onto the campaign, which geo-stamps
    every discovered Lead. Returns the seed's clause set, or ``[]`` when the ICP is
    empty.
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
