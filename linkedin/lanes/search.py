# linkedin/lanes/search.py
"""Search lane: discovers new profiles via LLM-generated LinkedIn search keywords."""
from __future__ import annotations

import logging

from django.utils import timezone
from termcolor import colored

logger = logging.getLogger(__name__)


class SearchLane:
    """Searches LinkedIn People when the pipeline is running low.

    Keywords are persisted in the DB (SearchKeyword model) so they
    survive daemon restarts. When all keywords are used, the LLM
    generates fresh ones (excluding previously-used keywords).
    """

    def __init__(self, session, qualifier):
        self.session = session
        self.qualifier = qualifier

    def can_execute(self) -> bool:
        self._ensure_keywords()
        from linkedin.models import SearchKeyword

        return SearchKeyword.objects.filter(
            campaign=self.session.campaign,
            used=False,
        ).exists()

    def execute(self):
        from linkedin.actions.search import search_people
        from linkedin.models import SearchKeyword

        kw = (
            SearchKeyword.objects.filter(
                campaign=self.session.campaign,
                used=False,
            )
            .order_by("pk")
            .first()
        )
        if not kw:
            return

        kw.used = True
        kw.used_at = timezone.now()
        kw.save()

        logger.info(
            colored("▶ search", "magenta", attrs=["bold"])
            + " keyword=%r",
            kw.keyword,
        )

        search_people(self.session, kw.keyword)

    def _ensure_keywords(self):
        """Refill the keyword queue from the LLM if no unused keywords remain."""
        from linkedin.models import SearchKeyword

        campaign = self.session.campaign

        if SearchKeyword.objects.filter(campaign=campaign, used=False).exists():
            return

        from linkedin.ml.search_keywords import generate_search_keywords

        used = list(
            SearchKeyword.objects.filter(campaign=campaign, used=True)
            .values_list("keyword", flat=True)
        )

        try:
            fresh = generate_search_keywords(
                product_docs=campaign.product_docs,
                campaign_objective=campaign.campaign_objective,
                exclude_keywords=used if used else None,
            )
        except Exception:
            logger.exception("Failed to generate search keywords")
            return

        if not fresh:
            return

        objs = [SearchKeyword(campaign=campaign, keyword=k) for k in fresh]
        SearchKeyword.objects.bulk_create(objs, ignore_conflicts=True)

        created = SearchKeyword.objects.filter(campaign=campaign, used=False).count()
        logger.info(
            "Loaded %d new search keywords (%d previously used)",
            created,
            len(used),
        )
