# linkedin/onboarding.py
"""Keyword generation from CLI file arguments."""
from __future__ import annotations

import logging
import re
from pathlib import Path

import jinja2
import yaml

from linkedin.conf import ASSETS_DIR, KEYWORDS_FILE

logger = logging.getLogger(__name__)


def generate_keywords(product_docs: str, objective: str) -> dict:
    """Call LLM to generate campaign keywords. Returns validated dict."""
    from linkedin.templates.renderer import call_llm

    template_dir = ASSETS_DIR / "templates" / "prompts"
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(template_dir)))
    template = env.get_template("generate_keywords.j2")
    prompt = template.render(product_docs=product_docs, campaign_objective=objective)

    logger.info("Calling LLM to generate campaign keywords...")
    response = call_llm(prompt)

    # Strip markdown code fences if present
    cleaned = re.sub(r"^```(?:yaml|yml)?\s*\n?", "", response, flags=re.MULTILINE)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned, flags=re.MULTILINE)

    data = yaml.safe_load(cleaned)
    if not isinstance(data, dict):
        raise ValueError(f"LLM response did not parse as YAML dict:\n{response}")

    for key in ("positive", "negative", "exploratory"):
        if key not in data or not isinstance(data[key], list):
            raise ValueError(f"Missing or invalid '{key}' list in LLM response")
        data[key] = [kw.lower().strip() for kw in data[key]]

    return data


def ensure_keywords(
    product_docs_path: str | None = None,
    campaign_objective_path: str | None = None,
) -> None:
    """Generate keywords when both file paths are provided via CLI flags.

    Called from cmd_run before the daemon starts. Does nothing unless
    both --product-docs and --campaign-objective are given.
    """
    if not product_docs_path or not campaign_objective_path:
        return

    product_docs = Path(product_docs_path).read_text(encoding="utf-8").strip()
    objective = Path(campaign_objective_path).read_text(encoding="utf-8").strip()

    data = generate_keywords(product_docs, objective)

    with open(KEYWORDS_FILE, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    logger.info("Keywords written to %s", KEYWORDS_FILE)
    logger.info(
        "  %d positive, %d negative, %d exploratory",
        len(data["positive"]), len(data["negative"]), len(data["exploratory"]),
    )
