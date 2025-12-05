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
        key: SessionKey identifying the persistent browser account_session
        profile: Must contain "url"

    Returns:
        Same profile dict, enriched with full LinkedIn data
    """
    url = profile["url"]

    account_session = AccountSessionRegistry.get_or_create(
        handle=key.handle,
        campaign_name=key.campaign_name,
        csv_hash=key.csv_hash,
    )
    resources = account_session.resources

    # ──────────────────────────────
    wait(resources)
    api = PlaywrightLinkedinAPI(resources=resources)

    enriched, raw_json = api.get_profile(profile_url=url)

    full_name = enriched.get("full_name")

    if not full_name:
        logger.warning(
            f"SKIPPING profile – no valid name found → {url} got: {full_name!r})"
        )
        return None, None

    logger.info(f"Profile enriched: {full_name} – {url}")

    # Only log the first 100 characters of the pretty-printed JSON
    pretty_json = json.dumps(enriched, indent=2, ensure_ascii=False, default=str)
    preview = '\n'.join(pretty_json.splitlines()[:10])  # first ~10 lines
    logger.debug("=== ENRICHED PROFILE PREVIEW (first 100 chars) ===")
    logger.debug(preview)
    logger.debug("=== END OF PREVIEW ===")

    return enriched, raw_json


def _save_profile_to_fixture(enriched_profile: Dict[str, Any], path) -> None:
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

    test_profile = {"url": "https://www.linkedin.com/in/lexfridman/"}

    _, raw_json = enrich_profile(key, test_profile)

    if raw_json:
        # Save to tests/fixtures/linkedin_profile.json
        _save_profile_to_fixture(raw_json, FIXTURE_PATH)
        print(f"Saved fixture → {FIXTURE_PATH}")
    else:
        print("Failed to enrich profile.")
