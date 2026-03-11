# linkedin/pipeline/search.py
"""Search keyword management and LinkedIn People search."""
from __future__ import annotations

import logging

from django.utils import timezone
from termcolor import colored

logger = logging.getLogger(__name__)


def run_search(session) -> str | None:
    """Use the next search keyword to discover new profiles. Returns keyword or None."""
    from linkedin.actions.search import search_people
    from linkedin.pipeline.search_keywords import generate_search_keywords
    from linkedin.models import SearchKeyword

    campaign = session.campaign

    if not SearchKeyword.objects.filter(campaign=campaign, used=False).exists():
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
            return None

        if not fresh:
            return None

        objs = [SearchKeyword(campaign=campaign, keyword=k) for k in fresh]
        SearchKeyword.objects.bulk_create(objs, ignore_conflicts=True)

    kw = (
        SearchKeyword.objects.filter(campaign=campaign, used=False)
        .order_by("pk")
        .first()
    )
    if not kw:
        return None

    kw.used = True
    kw.used_at = timezone.now()
    kw.save()

    logger.info(colored("\u25b6 search", "magenta", attrs=["bold"]) + " keyword=%r", kw.keyword)
    search_people(session, kw.keyword)
    return kw.keyword
