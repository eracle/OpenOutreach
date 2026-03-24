# linkedin/actions/profile.py
import json
import logging
from pathlib import Path
from typing import Dict, Any

from linkedin.conf import FIXTURE_PROFILES_DIR
from ..api.client import PlaywrightLinkedinAPI

logger = logging.getLogger(__name__)


def scrape_profile(session, profile: dict):
    url = profile["url"]

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


# python -m linkedin.actions.profile
if __name__ == "__main__":
    import os
    import argparse
    from pprint import pprint

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "linkedin.django_settings")

    import django
    django.setup()

    from linkedin.conf import resolve_profile
    from linkedin.browser.registry import get_or_create_session

    logging.basicConfig(
        level=logging.DEBUG,
        format="[%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(description="Scrape a LinkedIn profile")
    parser.add_argument("--handle", default=None, help="Django username (default: first active profile)")
    parser.add_argument("--profile", default="me", help="Public identifier of the target profile (default: me)")
    parser.add_argument("--save-fixture", action="store_true", help="Save raw data as test fixture")
    args = parser.parse_args()

    linkedin_profile = resolve_profile(args.handle)
    if not linkedin_profile:
        print("No active LinkedInProfile found.")
        raise SystemExit(1)

    test_profile = {
        "url": f"https://www.linkedin.com/in/{args.profile}/",
    }

    session = get_or_create_session(linkedin_profile)
    session.campaign = session.campaigns[0]
    print(f"Scraping profile as {session} → {args.profile}")

    profile, data = scrape_profile(session, test_profile)
    pprint(profile)

    if args.save_fixture:
        fixture_path = FIXTURE_PROFILES_DIR / "linkedin_profile.json"
        _save_profile_to_fixture(data, fixture_path)
