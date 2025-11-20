# linkedin/actions/profile.py
import csv
import logging
import os
from typing import Dict, Any, List

from linkedin.navigation.login import get_resources_with_state_management
from ..api.client import PlaywrightLinkedinAPI

logger = logging.getLogger(__name__)


def get_profiles(context: Dict[str, Any], linkedin_url: str):
    pass


def get_profile_info(context: Dict[str, Any], linkedin_url: str):
    """
    Retrieves profile information via an API call.
    """

    resources = context['resources']
    linkedin_api = PlaywrightLinkedinAPI(resources=resources)

    profile_dict, get_profile_json = linkedin_api.get_profile(profile_url=linkedin_url)
    return profile_dict


def read_urls(linkedin_url: str, params: Dict[str, Any]) -> List[str]:
    """
    Parses input CSVs and returns a list of URLs.
    """
    file_path = params.get('file_path')
    if not file_path or not os.path.exists(file_path):
        print(f"ACTION: read_urls - File not found at {file_path}")
        return []

    print(f"ACTION: read_urls from {file_path}")
    urls = []
    with open(file_path, 'r', newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            if 'url' in row:
                urls.append(row['url'])

    return urls


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

    linkedin_url = "https://www.linkedin.com/in/williamhgates/"

    profile = get_profile_info(context, linkedin_url)

    from pprint import pprint

    pprint(profile)

    # Clean up
    resources.context.close()
    resources.browser.close()
    resources.playwright.stop()
