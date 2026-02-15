# linkedin/lanes/search.py
"""Search lane: discovers new profiles via LLM-generated LinkedIn search keywords."""
from __future__ import annotations

import logging

from termcolor import colored

logger = logging.getLogger(__name__)


class SearchLane:
    """Searches LinkedIn People when the pipeline has nothing left to process.

    Lowest-priority gap-filler — only fires when enrich and qualify
    both have nothing to do.
    """

    def __init__(self, session, qualifier):
        self.session = session
        self.qualifier = qualifier
        self._keywords: list[str] = []
        self._searched: set[str] = set()

    def can_execute(self) -> bool:
        self._ensure_keywords()
        return len(self._keywords) > 0

    def execute(self):
        from linkedin.actions.search import search_people

        keyword = self._keywords.pop(0)
        self._searched.add(keyword)
        logger.info(
            colored("▶ search", "magenta", attrs=["bold"])
            + " keyword=%r",
            keyword,
        )

        search_people(self.session, keyword)

    def _ensure_keywords(self):
        """Refill the keyword queue from the LLM if empty."""
        if self._keywords:
            return

        from linkedin.ml.search_keywords import generate_search_keywords

        try:
            fresh = generate_search_keywords()
        except Exception:
            logger.exception("Failed to generate search keywords")
            return

        self._keywords = [k for k in fresh if k not in self._searched]
        if self._keywords:
            logger.info(
                "Loaded %d new search keywords (%d already used)",
                len(self._keywords),
                len(self._searched),
            )
