# linkedin/setup/seeds.py
"""User-provided seed profiles: parse URLs, create Leads + QUALIFIED Deals."""
from __future__ import annotations

import csv
import io
import logging

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


def _coerce_seed_state(initial_state: str | ProfileState) -> str:
    """Normalize a caller-provided initial state for seed imports."""
    if isinstance(initial_state, ProfileState):
        return initial_state.value
    return ProfileState(initial_state).value


def create_seed_leads(
    campaign,
    public_ids: list[str],
    *,
    initial_state: str | ProfileState = ProfileState.QUALIFIED,
) -> int:
    """Create url-only Leads + Deals for seed profiles.

    Works without a browser session — leads will be lazily enriched
    and embedded when the daemon processes them.

    Returns the number of new seeds created.
    """
    from crm.models import Deal, Lead

    initial_state = _coerce_seed_state(initial_state)
    existing_seeds = set(campaign.seed_public_ids or [])
    created = 0
    for public_id in public_ids:
        url = public_id_to_url(public_id)

        lead, _ = Lead.objects.get_or_create(linkedin_url=url, defaults={"public_identifier": public_id})

        if Deal.objects.filter(lead=lead, campaign=campaign).exists():
            logger.debug("Seed %s already has a deal, skipping", public_id)
            existing_seeds.add(public_id)
            continue

        Deal.objects.create(
            lead=lead,
            campaign=campaign,
            state=initial_state,
        )
        existing_seeds.add(public_id)
        created += 1
        logger.info("Seed %s → %s", public_id, initial_state)

    campaign.seed_public_ids = list(existing_seeds)
    campaign.save(update_fields=["seed_public_ids"])
    return created


def _normalize_columns(fieldnames: list[str]) -> dict[str, str]:
    """Map normalized (lowercase, stripped) column names to original names."""
    return {name.strip().lower(): name for name in fieldnames}


def parse_csv_leads(text: str) -> list[dict]:
    """Parse CSV text into a list of lead dicts with url, first_name, last_name, company_name.

    Raises ValueError if 'profile url' column is missing.
    """
    # Strip BOM if present (common in Excel-exported CSVs)
    text = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("Empty CSV or no header row")

    col_map = _normalize_columns(reader.fieldnames)

    url_col = col_map.get("profile url")
    if not url_col:
        raise ValueError(
            "CSV must have a 'Profile URL' column. "
            f"Found columns: {', '.join(reader.fieldnames)}"
        )

    first_col = col_map.get("first name")
    last_col = col_map.get("last name")
    company_col = col_map.get("company")

    leads = []
    for row in reader:
        url = (row.get(url_col) or "").strip()
        if not url:
            continue
        public_id = url_to_public_id(url)
        if not public_id:
            logger.warning("Skipping invalid LinkedIn URL: %s", url)
            continue
        leads.append({
            "url": url,
            "public_id": public_id,
            "first_name": (row.get(first_col) or "").strip() if first_col else "",
            "last_name": (row.get(last_col) or "").strip() if last_col else "",
            "company_name": (row.get(company_col) or "").strip() if company_col else "",
        })
    return leads


def create_seed_leads_from_csv(
    campaign,
    leads: list[dict],
    *,
    initial_state: str | ProfileState = ProfileState.QUALIFIED,
) -> int:
    """Create Leads with pre-populated names + Deals from CSV data.

    Returns the number of new seeds created.
    """
    from crm.models import Deal, Lead

    initial_state = _coerce_seed_state(initial_state)
    existing_seeds = set(campaign.seed_public_ids or [])
    created = 0
    for entry in leads:
        public_id = entry["public_id"]
        url = public_id_to_url(public_id)

        lead, _ = Lead.objects.get_or_create(
            linkedin_url=url,
            defaults={"public_identifier": public_id},
        )

        # Update name/company fields if provided and not already set
        updated_fields = []
        for field in ("first_name", "last_name", "company_name"):
            value = entry.get(field, "")
            if value and not getattr(lead, field):
                setattr(lead, field, value)
                updated_fields.append(field)
        if updated_fields:
            lead.save(update_fields=updated_fields)

        if Deal.objects.filter(lead=lead, campaign=campaign).exists():
            existing_seeds.add(public_id)
            continue

        Deal.objects.create(
            lead=lead,
            campaign=campaign,
            state=initial_state,
        )
        existing_seeds.add(public_id)
        created += 1
        logger.info("Seed %s → %s", public_id, initial_state)

    campaign.seed_public_ids = list(existing_seeds)
    campaign.save(update_fields=["seed_public_ids"])
    return created
