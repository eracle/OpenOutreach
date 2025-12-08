# linkedin/actions/profile.py
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from linkedin.conf import FIXTURE_PROFILES_DIR
from linkedin.sessions.registry import AccountSessionRegistry, SessionKey
from ..api.client import PlaywrightLinkedinAPI

logger = logging.getLogger(__name__)


def enrich_profile(key: SessionKey, profile: Dict[str, Any]) -> Tuple[Optional[Dict], Optional[Dict]]:
    """
    Enriches a profile using the PlaywrightLinkedinAPI by navigating to the profile URL.

    Args:
        key: SessionKey identifying the persistent browser session
        profile: Dict containing at least "url"

    Returns:
        (enriched_profile_dict, raw_json_response) or (None, None) if failed
    """
    url = profile["url"]

    # Get singleton session (auto-recover browser if crashed)
    session = AccountSessionRegistry.get_or_create(
        handle=key.handle,
        campaign_name=key.campaign_name,
        csv_hash=key.csv_hash,
    )
    session.ensure_browser()  # ← ensures browser + login are ready

    session.wait()

    # Pass the live session.page to the API client
    api = PlaywrightLinkedinAPI(session=session)

    logger.info("Enriching profile → %s", url)
    enriched, raw_json = api.get_profile(profile_url=url)

    if enriched is None:
        logger.warning("Failed to enrich profile (api returned None) → %s", url)
        return None, None

    full_name = enriched.get("full_name")
    if not full_name:
        logger.warning("No valid name found in enriched profile → %s (got: %r)", url, full_name)
        return None, None

    logger.info("Profile enriched successfully: %s – %s", full_name, url)

    # Pretty preview in logs
    pretty = json.dumps(enriched, indent=2, ensure_ascii=False, default=str)
    preview_lines = pretty.splitlines()[:12]
    logger.debug("=== ENRICHED PROFILE PREVIEW ===\n%s\n...", '\n'.join(preview_lines))

    return enriched, raw_json


def _save_profile_to_fixture(enriched_profile: Dict[str, Any], path: str | Path) -> None:
    """Utility to save enriched profile as test fixture."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(enriched_profile, f, indent=2, ensure_ascii=False, default=str)
    logger.info("Enriched profile saved to fixture → %s", path)


if __name__ == "__main__":
    import sys
    from linkedin.campaigns.connect_follow_up import INPUT_CSV_PATH

    FIXTURE_PATH = FIXTURE_PROFILES_DIR / "linkedin_profile.json"

    logging.getLogger().handlers.clear()
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s │ %(levelname)-8s │ %(message)s',
        datefmt="%H:%M:%S",
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

    test_profile = {
        "url": "https://www.linkedin.com/in/lexfridman/",
    }

    enriched, raw_json = enrich_profile(key, test_profile)

    if raw_json:
        _save_profile_to_fixture(raw_json, FIXTURE_PATH)
        print(f"Fixture saved → {FIXTURE_PATH}")
    else:
        print("Failed to enrich profile.")
