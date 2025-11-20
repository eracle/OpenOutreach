# linkedin/actions/profile.py
import logging
from typing import Dict, Any

from linkedin.actions.login import get_resources_with_state_management
from ..api.client import PlaywrightLinkedinAPI

logger = logging.getLogger(__name__)


def get_profile_info(context: Dict[str, Any], linkedin_url: str):
    """
    Retrieves profile information via an API call.
    """

    resources = context['resources']
    linkedin_api = PlaywrightLinkedinAPI(resources=resources)

    profile_dict, get_profile_json = linkedin_api.get_profile(profile_url=linkedin_url)
    return profile_dict


if __name__ == "__main__":
    # Forcefully reset and configure logging to ensure reliability
    root_logger = logging.getLogger()
    root_logger.handlers = []  # Clear any existing handlers to avoid conflicts
    logging.basicConfig(
        level=logging.DEBUG,  # Set to DEBUG for more logs; change to INFO if too verbose
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    resources = get_resources_with_state_management(use_state=True, force_login=False)
    context = dict(resources=resources)

    linkedin_url = "https://www.linkedin.com/in/ylenia-chiarvesio-59122844/"

    profile = get_profile_info(context, linkedin_url)

    from pprint import pprint

    pprint(profile)

    # Clean up
    resources.context.close()
    resources.browser.close()
    resources.playwright.stop()
