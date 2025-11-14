# linkedin/actions/profile.py
from urllib.parse import urlparse

from linkedin.actions.login import build_playwright
from ..api.client import PlaywrightLinkedinAPI


def get_profile_info(playwright_linkedin, linkedin_url: str):
    """
    Retrieves profile information via an API call.
    """
    public_id = urlparse(linkedin_url).path.split('/')[2]
    profile_dict = None  # get_profile_data(playwright_linkedin=playwright_linkedin, public_id=public_id)
    return profile_dict


if __name__ == "__main__":
    # Build the page with login
    resources = build_playwright(login=True)

    # Wait a bit after login to observe
    resources.page.wait_for_timeout(5000)

    playwright_linkedin_api = PlaywrightLinkedinAPI(resources=resources)

    public_id = 'eracle'

    profile = playwright_linkedin_api.get_profile(public_id=public_id)

    from pprint import pprint

    pprint(profile)

    # Clean up
    resources.context.close()
    resources.browser.close()
    resources.playwright.stop()
