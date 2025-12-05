# linkedin/api/client.py
import logging
from typing import Optional, Dict, Tuple
from urllib.parse import urlparse

from linkedin.api.voyager import parse_linkedin_voyager_response
from linkedin.navigation.errors import AuthenticationError

logger = logging.getLogger(__name__)


class PlaywrightLinkedinAPI:

    def __init__(
            self,
            resources=None,
            *,
            debug=False,
    ):
        """Constructor method"""
        if not resources:
            raise ValueError("Playwright resources must be provided.")

        # This is safe even if PlaywrightResources changes order or adds fields
        self.page = resources.page
        self.context = resources.context
        self.browser = resources.browser
        self.playwright = resources.playwright
        logger.info("Using provided Playwright resources for requests.")

        # Extract cookies from the browser context to get JSESSIONID for csrf-token
        cookies = self.context.cookies()
        cookies_dict = {c['name']: c['value'] for c in cookies}
        jsessionid = cookies_dict.get('JSESSIONID', '').strip('"')

        # Dynamically fetch browser-specific details using page.evaluate
        user_agent = self.page.evaluate("navigator.userAgent")
        accept_language = self.page.evaluate("navigator.languages ? navigator.languages.join(',') : navigator.language")
        sec_ch_ua = self.page.evaluate("""() => {
            if (navigator.userAgentData) {
                return navigator.userAgentData.brands.map(brand => `"${brand.brand}";v="${brand.version}"`).join(', ');
            }
            return '';
        }""")
        sec_ch_ua_mobile = self.page.evaluate(
            """() => navigator.userAgentData ? (navigator.userAgentData.mobile ? '?1' : '?0') : '?0' """)
        sec_ch_ua_platform = self.page.evaluate(
            """() => navigator.userAgentData ? `"${navigator.userAgentData.platform}"` : '' """)

        # Set up headers with dynamic values
        self.headers = {
            'accept': 'application/vnd.linkedin.normalized+json+2.1',
            'accept-language': accept_language,
            'csrf-token': jsessionid,
            'priority': 'u=1, i',
            'referer': self.page.url,
            'sec-ch-prefers-color-scheme': 'light',  # This might need dynamic detection if possible
            'sec-ch-ua': sec_ch_ua,
            'sec-ch-ua-mobile': sec_ch_ua_mobile,
            'sec-ch-ua-platform': sec_ch_ua_platform,
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'user-agent': user_agent,
            'x-li-lang': 'en_US',
            'x-restli-protocol-version': '2.0.0',
        }

        logging.basicConfig(level=logging.DEBUG if debug else logging.INFO)
        self.logger = logger

    def get_profile(
            self, public_identifier: Optional[str] = None, profile_url: Optional[str] = None
    ) -> Tuple[Dict, Dict]:
        """Fetch data for a given LinkedIn profile using Playwright context requests.

        :param public_identifier: LinkedIn public ID for a profile
        :type public_identifier: str, optional
        :param profile_url: Full LinkedIn profile URL
        :type profile_url: str, optional

        :return: A pair of dictionaries: (parsed_data, original_data)
        :rtype: tuple[dict, dict]
        """
        if not public_identifier and profile_url:
            public_identifier = urlparse(profile_url).path.strip('/').split('/')[-1]

        if not public_identifier:
            raise ValueError("Either public_identifier or profile_url must be provided.")

        params = {
            'decorationId': 'com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-91',
            'memberIdentity': public_identifier,
            'q': 'memberIdentity',
        }

        base_url = "https://www.linkedin.com/voyager/api"
        uri = "/identity/dash/profiles"
        full_url = base_url + uri

        # Use Playwright context request to fetch API data
        res = self.context.request.get(full_url, params=params, headers=self.headers)

        if res.status == 401:
            self.logger.error(f"Authentication failed (401): {res.body()}")
            raise AuthenticationError("LinkedIn API returned 401 Unauthorized.")
        elif not res.ok:
            self.logger.info(f"Request failed with status {res.status}: {res.body()}")
            return {}, {}

        data = res.json()
        extracted_info = parse_linkedin_voyager_response(data, public_identifier=public_identifier)
        return extracted_info, data
