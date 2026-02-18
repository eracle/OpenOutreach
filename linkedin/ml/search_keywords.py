# linkedin/ml/search_keywords.py
"""LLM-based generation of LinkedIn People search keywords."""
from __future__ import annotations

import logging

import jinja2
from pydantic import BaseModel, Field

from linkedin.conf import PROMPTS_DIR

logger = logging.getLogger(__name__)


class SearchKeywords(BaseModel):
    """Structured LLM output for search keyword generation."""
    keywords: list[str] = Field(description="List of LinkedIn People search queries")


def generate_search_keywords(
    product_docs: str,
    campaign_objective: str,
    n_keywords: int = 10,
    exclude_keywords: list[str] | None = None,
) -> list[str]:
    """Call LLM to generate LinkedIn search keywords from campaign context.

    Returns a list of search query strings.
    """
    from langchain_openai import ChatOpenAI

    from linkedin.conf import AI_MODEL, LLM_API_KEY, LLM_API_BASE

    if LLM_API_KEY is None:
        raise ValueError("LLM_API_KEY is not set in the environment or config.")

    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(PROMPTS_DIR)))
    template = env.get_template("search_keywords.j2")

    prompt = template.render(
        product_docs=product_docs,
        campaign_objective=campaign_objective,
        n_keywords=n_keywords,
        exclude_keywords=exclude_keywords or [],
    )

    llm = ChatOpenAI(model=AI_MODEL, temperature=0.9, api_key=LLM_API_KEY, base_url=LLM_API_BASE)
    structured_llm = llm.with_structured_output(SearchKeywords)
    result = structured_llm.invoke(prompt)

    logger.info("Generated %d search keywords via LLM", len(result.keywords))
    return result.keywords
