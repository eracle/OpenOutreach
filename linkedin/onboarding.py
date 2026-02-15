# linkedin/onboarding.py
"""Onboarding: collect product docs and campaign objective."""
from __future__ import annotations

import logging

from linkedin.conf import (
    CAMPAIGN_DIR,
    CAMPAIGN_OBJECTIVE_FILE,
    PRODUCT_DOCS_FILE,
)

logger = logging.getLogger(__name__)


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
    """Prompt user for product description and campaign objective."""
    print()
    print("=" * 60)
    print("  OpenOutreach — Campaign Setup")
    print("=" * 60)
    print()
    print("To qualify LinkedIn profiles, we need two things:")
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

    print()
    print("Campaign setup complete!")
    print()


def ensure_onboarding() -> None:
    """Ensure product docs and campaign objective exist before the daemon starts.

    If both files already exist, does nothing (already onboarded).
    Otherwise, runs interactive onboarding to collect inputs.
    """
    if PRODUCT_DOCS_FILE.exists() and CAMPAIGN_OBJECTIVE_FILE.exists():
        return

    _interactive_onboarding()
