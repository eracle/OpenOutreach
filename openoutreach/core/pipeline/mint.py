# openoutreach/core/pipeline/mint.py
"""Clause minting — grow the axes from the leads that qualified.

The LLM is asked exactly twice in discovery: once at cold start for the seed
(``icp.generate_seed``), and here, to widen the vocabulary. Minting reads the
**qualified** profiles — the real titles, seniorities and locations of people the LLM
accepted — and proposes new clause *values* adjacent to them. A new value in an
existing family widens the Cartesian product of maximals; a new family (department,
function) deepens every maximal. Both are just "add clauses to the pool and recompose."

It never invents a whole query and never mints headcount (the campaign's fixed ICP
band). Values are real-world search terms — a made-up region or department matches
nothing and wastes a fetch — so the prompt supplies plausible values and the schema
constrains only ``lead_seniority`` (a closed vocabulary).

Minting is triggered two ways by ``discover`` (see that module): **throughput** —
every ``MINT_EVERY_N_QUALIFIED`` new qualified leads, fold in what they taught us;
and **saturation** — the pool spans nothing fetchable, so widen or stop. Neither is a
GP-confidence gate: a confidence gate would never fire at cold start, exactly the
deadlock this avoids. See the roadmap card ``p2-e3-discovery-unified-gp-query-selection``.
"""
from __future__ import annotations

import logging

import jinja2
from pydantic import BaseModel, ConfigDict, Field
from termcolor import colored

from openoutreach.core.conf import PROMPTS_DIR
from openoutreach.discovery import LEAD_SENIORITIES, Seniority, describe_filters, filters_for

logger = logging.getLogger(__name__)

# Qualified profiles shown to the mint prompt — the positive examples it generalizes.
QUALIFIED_SAMPLE_LIMIT = 30


def _render(clauses) -> str:
    """A clause bag as a readable list — per clause, since a mint spans several
    values per family and ``filters_for`` (one value per family) would raise on the
    whole set."""
    return ", ".join(describe_filters(filters_for([c])) for c in clauses)


class _MintedClauses(BaseModel):
    """New clause values to add to the pool, by family.

    Lists, not scalars: minting adds several values at once, each of which becomes a
    new axis position. ``extra="forbid"`` keeps the inert families (``lead_industry``,
    ``company_technology``, ``lead_skills``) unrepresentable — pydantic otherwise
    ignores unknown keys, reproducing the provider's silent-drop bug in our own code.
    Headcount is absent by design: it is the campaign's ICP band, not a value to mint.
    """

    model_config = ConfigDict(extra="forbid")

    lead_job_title: list[str] = Field(
        default_factory=list, description="New role titles adjacent to the qualified "
        "leads, e.g. 'VP of Sales'. Plain titles, no boolean syntax.")
    lead_seniority: list[Seniority] = Field(
        default_factory=list, description="New seniority levels, each from the fixed set.")
    lead_location: list[str] = Field(
        default_factory=list, description="New countries by real name, e.g. 'Canada'. "
        "Regions ('Europe', 'APAC') are not countries and match zero leads.")
    lead_department: list[str] = Field(
        default_factory=list, description="Departments by plain name, e.g. 'Sales', "
        "'Marketing', 'Human Resources'.")
    lead_function: list[str] = Field(
        default_factory=list, description="Broad job functions by plain name, e.g. "
        "'Operations', 'Legal'.")


def _qualified_profile_texts(campaign) -> list[str]:
    """Firmographic text of the leads the LLM accepted, newest first, capped.

    Qualified mirrors the GP's own labelling: any deal that is not an LLM rejection
    (``FAILED`` + ``wrong_fit``). These are the positive examples minting generalizes.
    """
    from openoutreach.crm.models import Deal, DealState, Lead, Outcome

    lead_ids = list(
        Deal.objects.filter(campaign=campaign, lead_id__isnull=False)
        .exclude(state=DealState.FAILED, outcome=Outcome.WRONG_FIT)
        .order_by("-pk").values_list("lead_id", flat=True)
    )
    texts = dict(
        Lead.objects.filter(pk__in=lead_ids).values_list("pk", "profile_text")
    )
    ordered = [texts.get(lid, "") for lid in lead_ids]
    return [t for t in ordered if t][:QUALIFIED_SAMPLE_LIMIT]


def mint_clauses(campaign) -> int:
    """Ask the LLM for new clause values and add the fresh ones to the pool.

    Returns the number of genuinely new clauses added (0 if the LLM proposed nothing
    unseen). Records the qualified count it minted at, so the throughput trigger does
    not re-fire until more leads qualify. Best-effort: an LLM outage adds nothing and
    the walk carries on with the pool it has.
    """
    from openoutreach.core.models import Clause

    qualified = _qualified_profile_texts(campaign)
    pool = sorted(campaign.clauses.values_list("family", "value"))

    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(PROMPTS_DIR)))
    prompt = env.get_template("mint_clauses.j2").render(
        product_docs=campaign.product_docs,
        campaign_target=campaign.campaign_target,
        seniorities=LEAD_SENIORITIES,
        pool=[describe_filters(filters_for([c])) for c in pool],
        qualified=qualified,
    )

    try:
        from pydantic_ai import Agent

        from openoutreach.core.llm import get_llm_model, run_agent_sync

        agent = Agent(
            get_llm_model(),
            output_type=_MintedClauses,
            model_settings={"temperature": 0.7, "timeout": 60},
        )
        minted = run_agent_sync(agent.run(prompt)).output
    except Exception:
        logger.exception("[%s] clause minting failed — pool unchanged", campaign)
        return 0

    proposal = [
        (family, str(value))
        for family, values in minted.model_dump().items()
        for value in values
    ]
    existing = set(pool)
    fresh = [pair for pair in proposal if pair not in existing]

    # Stamp the qualified count we just minted at, so the throughput trigger
    # (discover) does not re-fire until more leads qualify.
    from openoutreach.crm.models import Deal, DealState, Outcome
    campaign.discovery_minted_at_qualified = (
        Deal.objects.filter(campaign=campaign, lead_id__isnull=False)
        .exclude(state=DealState.FAILED, outcome=Outcome.WRONG_FIT).count()
    )
    campaign.save(update_fields=["discovery_minted_at_qualified"])

    if not fresh:
        logger.info("[%s] %s: nothing new from %d qualified example(s)",
                    campaign, colored("mint", "magenta", attrs=["bold"]), len(qualified))
        return 0

    campaign.clauses.add(*Clause.rows_for(fresh))
    logger.info("[%s] %s: +%d clause(s) from %d qualified example(s) — %s",
                campaign, colored("mint", "magenta", attrs=["bold"]),
                len(fresh), len(qualified), _render(fresh))
    return len(fresh)
