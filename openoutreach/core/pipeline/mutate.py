# openoutreach/core/pipeline/mutate.py
"""LLM-driven query mutation — propose one new Lead Finder filter set (MVP).

The frontier's breadth move: given a campaign's past query nodes and their
measured value, ask the LLM for a *single* new, distinct parameter set to try.
One query per call — the frontier widens a fetch at a time as its breadth floor
drains, and a single filter dict is what the model reliably produces. Kept behind
a small swappable interface (``set_generator``) so the learned cluster→query
replacement (roadmap Future work) can drop in without touching the frontier.

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

logger = logging.getLogger(__name__)

# Cap on past queries listed in the prompt — the crude bound on prompt growth.
PAST_QUERY_LIMIT = 40


class MutationGenerator(Protocol):
    """A callable proposing one new Lead Finder filter dict (or empty for none)."""

    def __call__(self, campaign) -> dict: ...


class _FilterSet(BaseModel):
    """The LLM's proposed Lead Finder filter dict."""

    filters: dict = Field(default_factory=dict)


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
    return run_agent_sync(agent.run(prompt)).output.filters


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
