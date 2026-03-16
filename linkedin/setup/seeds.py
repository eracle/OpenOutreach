# linkedin/setup/seeds.py
"""User-provided seed profiles: parse URLs, create Leads + QUALIFIED Deals."""
from __future__ import annotations

import logging

from linkedin.db._helpers import _get_lead_source, _get_stage, _make_ticket
from linkedin.db.urls import public_id_to_url, url_to_public_id
from linkedin.enums import ProfileState

logger = logging.getLogger(__name__)


def parse_seed_urls(text: str) -> list[str]:
    """Parse newline-separated LinkedIn URLs into public identifiers.

    Skips blank lines and invalid URLs. Returns deduplicated public IDs.
    """
    public_ids: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        public_id = url_to_public_id(line)
        if not public_id:
            logger.warning("Skipping invalid LinkedIn URL: %s", line)
            continue
        public_ids.add(public_id)
    return list(public_ids)


def create_seed_leads(campaign, public_ids: list[str]) -> int:
    """Create url-only Leads + QUALIFIED Deals for seed profiles.

    Works without a browser session — leads will be lazily enriched
    and embedded when the daemon processes them.

    Returns the number of new seeds created.
    """
    from crm.models import Deal, Lead

    dept = campaign.department
    stage = _get_stage(ProfileState.QUALIFIED, campaign)
    lead_source = _get_lead_source(campaign)
    user = dept.user_set.first()
    if not user:
        logger.error("No users in department %s — cannot create seed leads", dept.name)
        return 0

    existing_seeds = set(campaign.seed_public_ids or [])
    created = 0
    for public_id in public_ids:
        url = public_id_to_url(public_id)

        lead, _ = Lead.objects.get_or_create(
            website=url,
            defaults={
                "owner": user,
                "department": dept,
                "lead_source": lead_source,
            },
        )

        if Deal.objects.filter(lead=lead, department=dept).exists():
            logger.debug("Seed %s already has a deal, skipping", public_id)
            existing_seeds.add(public_id)
            continue

        Deal.objects.create(
            name=f"Seed: {public_id}",
            lead=lead,
            stage=stage,
            owner=user,
            department=dept,
            next_step="",
            next_step_date=None,
            ticket=_make_ticket(),
        )
        existing_seeds.add(public_id)
        created += 1
        logger.info("Seed %s → QUALIFIED", public_id)

    campaign.seed_public_ids = list(existing_seeds)
    campaign.save(update_fields=["seed_public_ids"])
    return created
