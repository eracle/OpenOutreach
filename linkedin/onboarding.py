# linkedin/onboarding.py
"""Keyword generation: interactive onboarding."""
from __future__ import annotations

import logging
import re

import jinja2
import yaml

from linkedin.conf import (
    ASSETS_DIR,
    CAMPAIGN_DIR,
    CAMPAIGN_OBJECTIVE_FILE,
    KEYWORDS_FILE,
    PRODUCT_DOCS_FILE,
)

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


def _save_keywords(data: dict) -> None:
    """Write keywords dict to KEYWORDS_FILE."""
    CAMPAIGN_DIR.mkdir(parents=True, exist_ok=True)
    with open(KEYWORDS_FILE, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
    logger.info("Keywords written to %s", KEYWORDS_FILE)
    logger.info(
        "  %d positive, %d negative, %d exploratory",
        len(data["positive"]),
        len(data["negative"]),
        len(data["exploratory"]),
    )


def _read_multiline(prompt_msg: str) -> str:
    """Read multi-line input via input() until Ctrl-D (EOF)."""
    print(prompt_msg, flush=True)
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _interactive_onboarding() -> None:
    """Prompt user for product description and campaign objective, then generate keywords."""
    print()
    print("=" * 60)
    print("  OpenOutreach — Campaign Keyword Setup")
    print("=" * 60)
    print()
    print("To rank LinkedIn profiles, we need two things:")
    print("  1. A description of your product/service")
    print("  2. Your campaign objective (e.g. 'sell X to Y')")
    print()

    # Product description (multi-line)
    while True:
        product_docs = _read_multiline(
            "Paste your product/service description below.\n"
            "Press Ctrl-D when done:\n"
        )
        if product_docs:
            break
        print("Product description cannot be empty. Please try again.\n")

    print()

    # Campaign objective (multi-line)
    while True:
        objective = _read_multiline(
            "Enter your campaign objective (e.g. 'sell analytics platform to CTOs').\n"
            "Press Ctrl-D when done:\n"
        )
        if objective:
            break
        print("Campaign objective cannot be empty. Please try again.\n")

    # Persist inputs
    CAMPAIGN_DIR.mkdir(parents=True, exist_ok=True)
    PRODUCT_DOCS_FILE.write_text(product_docs, encoding="utf-8")
    CAMPAIGN_OBJECTIVE_FILE.write_text(objective, encoding="utf-8")
    logger.info("Saved product docs to %s", PRODUCT_DOCS_FILE)
    logger.info("Saved campaign objective to %s", CAMPAIGN_OBJECTIVE_FILE)

    # Generate keywords via LLM
    data = generate_keywords(product_docs, objective)
    _save_keywords(data)

    print()
    print("Keywords generated successfully!")
    print(f"  {len(data['positive'])} positive, {len(data['negative'])} negative, {len(data['exploratory'])} exploratory")
    print()


def ensure_keywords() -> None:
    """Ensure campaign keywords exist before the daemon starts.

    If keywords already exist, does nothing (already onboarded).
    Otherwise, runs interactive onboarding to collect inputs and generate keywords.
    """
    if KEYWORDS_FILE.exists():
        return

    _interactive_onboarding()
