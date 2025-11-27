# linkedin/actions/profile.py
import logging
from pathlib import Path
from typing import Dict, Any

from ..api.client import PlaywrightLinkedinAPI

logger = logging.getLogger(__name__)


def enrich_profile(automation: "LinkedInAutomation", profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    SCRAPE STEP handler

    Args:
        automation: LinkedInAutomation singleton
        profile: Must contain "linkedin_url"

    Returns:
        Same profile dict, enriched with full LinkedIn data
    """
    linkedin_url = profile["linkedin_url"]
    resources = automation.browser

    api = PlaywrightLinkedinAPI(resources=resources)

    profile_data, raw_json = api.get_profile(profile_url=linkedin_url)

    # Merge everything
    enriched = {
        "linkedin_url": linkedin_url,
        **profile_data,
    }

    full_name = profile_data.get("full_name") or profile_data.get("name") or "Unknown"
    logger.info(f"Profile enriched: {full_name} – {linkedin_url}")

    return enriched


if __name__ == "__main__":
    import sys
    from linkedin.automation import AutomationRegistry

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

    # ← ONLY THIS BLOCK CHANGED – use registry
    automation = AutomationRegistry.get_or_create(
        handle=handle,
        campaign_name="test_profile",
        csv_hash="debug",
        input_csv=Path("dummy.csv"),
    )

    test_profile = {"linkedin_url": "https://www.linkedin.com/in/williamhgates/"}

    enriched = enrich_profile(automation, test_profile)

    print(f"Enriched profile for {enriched}")

