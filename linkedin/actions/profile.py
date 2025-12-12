# linkedin/actions/profile.py
import json
import logging
from pathlib import Path
from typing import Dict, Any

from linkedin.conf import FIXTURE_PROFILES_DIR
from linkedin.sessions.registry import AccountSessionRegistry, SessionKey
from ..api.client import PlaywrightLinkedinAPI

logger = logging.getLogger(__name__)


def scrape_profile(key: SessionKey, profile: dict):
    url = profile["url"]

    session = AccountSessionRegistry.get_or_create(
        handle=key.handle,
        campaign_name=key.campaign_name,
        csv_hash=key.csv_hash,
    )

    # ── Existing enrichment logic (100% unchanged) ──
    session.ensure_browser()
    session.wait()

    api = PlaywrightLinkedinAPI(session=session)

    logger.info("Enriching profile → %s", url)
    profile, data = api.get_profile(profile_url=url)

    logger.info("Profile enriched – %s", profile.get("public_identifier")) if profile else None

    return profile, data


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

    profile, data = scrape_profile(key, test_profile)

    _save_profile_to_fixture(data, FIXTURE_PATH)
    print(f"Fixture saved → {FIXTURE_PATH}")
