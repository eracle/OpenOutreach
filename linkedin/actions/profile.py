# linkedin/actions/profile.py
import json
import logging
import os
from pathlib import Path
from typing import Dict, Any

from linkedin.navigation.utils import wait
from linkedin.sessions import AccountSessionRegistry, SessionKey  # ← added SessionKey
from ..api.client import PlaywrightLinkedinAPI

logger = logging.getLogger(__name__)



def enrich_profile(key: SessionKey, profile: Dict[str, Any]):
    """
    SCRAPE STEP handler

    Args:
        key: SessionKey identifying the persistent browser session
        profile: Must contain "linkedin_url"

    Returns:
        Same profile dict, enriched with full LinkedIn data
    """
    linkedin_url = profile["linkedin_url"]

    session = AccountSessionRegistry.get_or_create(
        handle=key.handle,
        campaign_name=key.campaign_name,
        csv_hash=key.csv_hash,
    )
    resources = session.resources

    # ──────────────────────────────
    wait(resources)
    api = PlaywrightLinkedinAPI(resources=resources)

    profile_data, raw_json = api.get_profile(profile_url=linkedin_url)

    full_name = profile_data.get("full_name")

    if not full_name:
        logger.warning(
            f"SKIPPING profile – no valid name found → {linkedin_url} got: {full_name!r})"
        )
        return None, None

    # Merge everything
    enriched = {
        "linkedin_url": linkedin_url,
        **profile_data,
    }

    logger.info(f"Profile enriched: {full_name} – {linkedin_url}")

    # Optional: still log full debug info if needed
    logger.debug("=== ENRICHED PROFILE DATA ===")
    logger.debug(f"Enriched keys ({len(enriched)} total): {list(enriched.keys())}")
    pretty_json = json.dumps(enriched, indent=2, ensure_ascii=False, default=str)
    for line in pretty_json.splitlines():
        logger.debug(line)
    logger.debug("=== END OF ENRICHED PROFILE ===")

    return enriched, raw_json


def _save_profile_to_fixture(enriched_profile: Dict[str, Any], path: str = FIXTURE_PATH) -> None:
    """Utility to save the enriched profile as a test fixture."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(enriched_profile, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f"Enriched profile saved to fixture: {path}")


if __name__ == "__main__":
    import sys
    from linkedin.campaigns.connect_follow_up import INPUT_CSV_PATH

    # Define the fixture path relative to the project root
    FIXTURE_PATH = os.path.join(Path(__file__).parent.parent.parent, "tests", "fixtures", "linkedin_profile.json")

    root_logger = logging.getLogger()
    root_logger.handlers = []
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    if len(sys.argv) != 2:
        print("Usage: python -m linkedin.actions.profile <handle>")
        sys.exit(1)

    handle = sys.argv[1]

    key = SessionKey.make(
        handle=handle,
        campaign_name="test_profile",
        csv_path=INPUT_CSV_PATH,
    )

    test_profile = {"linkedin_url": "https://www.linkedin.com/in/williamhgates/"}

    _, raw_json = enrich_profile(key, test_profile)

    if raw_json:
        # Save to tests/fixtures/linkedin_profile.json
        _save_profile_to_fixture(raw_json)
        print(f"Saved fixture → {FIXTURE_PATH}")
    else:
        print("Failed to enrich profile.")
