# linkedin/seeds.py
"""Seed URL loader: load "good prospect" URLs from CSV and create DISCOVERED leads."""
from __future__ import annotations

import csv
import json
import logging

from linkedin.conf import SEED_URLS_FILE

logger = logging.getLogger(__name__)


def load_seed_urls() -> list[str]:
    """Read seed URLs CSV (header: url) and return list of LinkedIn URLs."""
    urls = []
    with open(SEED_URLS_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            url = (row.get("url") or "").strip()
            if url:
                urls.append(url)

    logger.debug("Loaded %d seed URLs from %s", len(urls), SEED_URLS_FILE)
    return urls


def ensure_seeds(session) -> int:
    """Create DISCOVERED leads for seed URLs, tagged with next_step={"seed": true}.

    Returns the count of newly created seed leads.
    """
    from crm.models import Deal, Lead

    from linkedin.db.crm_profiles import (
        add_profile_urls,
        public_id_to_url,
        url_to_public_id,
    )

    urls = load_seed_urls()
    if not urls:
        return 0

    # Create leads (idempotent — skips existing)
    add_profile_urls(session, urls)

    # Tag seed deals with {"seed": true} in next_step
    count = 0
    for url in urls:
        try:
            public_id = url_to_public_id(url)
        except ValueError:
            continue

        clean_url = public_id_to_url(public_id)
        lead = Lead.objects.filter(website=clean_url).first()
        if not lead:
            continue

        deal = Deal.objects.filter(lead=lead).first()
        if not deal:
            continue

        # Only tag if not already tagged
        try:
            meta = json.loads(deal.next_step) if deal.next_step else {}
        except (json.JSONDecodeError, TypeError):
            meta = {}

        if not meta.get("seed"):
            meta["seed"] = True
            Deal.objects.filter(pk=deal.pk).update(
                next_step=json.dumps(meta),
            )
            count += 1

    if count:
        logger.info("Tagged %d seed profiles", count)
    return count
