# openoutreach/core/pipeline/icp.py
"""ICP filter generator — LLM maps a campaign to a Lead Finder search spec.

A single LLM pass turns ``product_docs + campaign_target`` into firmographic
filters (title/seniority/industry/location/headcount) that Lead Finder discovery
searches on. Generated once per campaign and cached on ``Campaign.icp_filters``
so pagination stays coherent across cycles. Adaptive refinement (the active
learner tightening the spec from outcomes) is a future redesign — see the
off-linkedin-leadfinder-pivot roadmap card.
"""
from __future__ import annotations

import logging

import jinja2
from pydantic import BaseModel, Field

from openoutreach.core.conf import PROMPTS_DIR

logger = logging.getLogger(__name__)


class ICPSpec(BaseModel):
    """The LLM's provider-agnostic ICP output."""

    job_titles: list[str] = Field(default_factory=list)
    seniorities: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    headcount_min: int = 1
    headcount_max: int = 10000
    country_code: str = ""


def _to_lead_finder_filters(spec: ICPSpec) -> dict:
    """Map the provider-agnostic ICP onto Lead Finder's filter shape."""
    filters: dict = {
        "company_headcount_min": spec.headcount_min,
        "company_headcount_max": spec.headcount_max,
    }
    if spec.job_titles:
        filters["lead_job_title"] = {"include": spec.job_titles, "exact_match": False}
    if spec.seniorities:
        filters["lead_seniority"] = {"include": spec.seniorities}
    if spec.industries:
        filters["lead_industry"] = {"include": spec.industries}
    if spec.locations:
        filters["lead_location"] = {"include": spec.locations}
    return filters


def generate_icp_spec(campaign) -> dict:
    """LLM-generate the ICP spec for a campaign (single pass).

    Returns ``{"filters": <Lead Finder filter dict>, "country_code": "xx"}``.
    """
    from pydantic_ai import Agent

    from openoutreach.core.llm import get_llm_model, run_agent_sync

    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(PROMPTS_DIR)))
    prompt = env.get_template("icp_filters.j2").render(
        product_docs=campaign.product_docs,
        campaign_target=campaign.campaign_target,
    )

    agent = Agent(
        get_llm_model(),
        output_type=ICPSpec,
        model_settings={"temperature": 0.3, "timeout": 60},
    )
    spec = run_agent_sync(agent.run(prompt)).output
    return {"filters": _to_lead_finder_filters(spec), "country_code": spec.country_code.lower()}


def icp_for(campaign) -> dict:
    """Return the campaign's cached ICP spec, generating and persisting it once."""
    if campaign.icp_filters:
        return campaign.icp_filters
    spec = generate_icp_spec(campaign)
    campaign.icp_filters = spec
    campaign.save(update_fields=["icp_filters"])
    logger.info("[%s] ICP generated: %s", campaign, spec["filters"])
    return spec
