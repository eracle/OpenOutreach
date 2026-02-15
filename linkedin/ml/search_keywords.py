# linkedin/ml/search_keywords.py
"""LLM-based generation of LinkedIn People search keywords."""
from __future__ import annotations

import logging

import jinja2
from pydantic import BaseModel, Field

from linkedin.conf import ASSETS_DIR, CAMPAIGN_OBJECTIVE_FILE, PRODUCT_DOCS_FILE

logger = logging.getLogger(__name__)


class SearchKeywords(BaseModel):
    """Structured LLM output for search keyword generation."""
    keywords: list[str] = Field(description="List of LinkedIn People search queries")


def generate_search_keywords(n_keywords: int = 10) -> list[str]:
    """Call LLM to generate LinkedIn search keywords from campaign context.

    Returns a list of search query strings.
    """
    from langchain_openai import ChatOpenAI

    from linkedin.conf import AI_MODEL, LLM_API_KEY, LLM_API_BASE

    if LLM_API_KEY is None:
        raise ValueError("LLM_API_KEY is not set in the environment or config.")

    template_dir = ASSETS_DIR / "templates" / "prompts"
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(template_dir)))
    template = env.get_template("search_keywords.j2")

    if not PRODUCT_DOCS_FILE.exists():
        raise FileNotFoundError(f"Product docs not found: {PRODUCT_DOCS_FILE}")
    if not CAMPAIGN_OBJECTIVE_FILE.exists():
        raise FileNotFoundError(f"Campaign objective not found: {CAMPAIGN_OBJECTIVE_FILE}")

    product_docs = PRODUCT_DOCS_FILE.read_text(encoding="utf-8")
    campaign_objective = CAMPAIGN_OBJECTIVE_FILE.read_text(encoding="utf-8")

    prompt = template.render(
        product_docs=product_docs,
        campaign_objective=campaign_objective,
        n_keywords=n_keywords,
    )

    llm = ChatOpenAI(model=AI_MODEL, temperature=0.9, api_key=LLM_API_KEY, base_url=LLM_API_BASE)
    structured_llm = llm.with_structured_output(SearchKeywords)
    result = structured_llm.invoke(prompt)

    logger.info("Generated %d search keywords via LLM", len(result.keywords))
    return result.keywords
