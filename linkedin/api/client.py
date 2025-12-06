# linkedin/api/client.py
import json
import logging
from typing import Optional, Any
from urllib.parse import urlparse

from linkedin.api.voyager import parse_linkedin_voyager_response
from linkedin.navigation.errors import AuthenticationError
from linkedin.sessions import AccountSession

logger = logging.getLogger(__name__)


class PlaywrightLinkedinAPI:

    def __init__(
            self,
            session: AccountSession,
    ):
        self.session = session
        self.page = session.page
        self.context = session.context

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

    def get_profile(
            self, public_identifier: Optional[str] = None, profile_url: Optional[str] = None
    ) -> tuple[None, None] | tuple[dict, Any]:
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
            raise ValueError("Need public_identifier or profile_url")

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

        match res.status:
            case 401:
                logger.error(f"Authentication failed (401): {res.body()}")
                raise AuthenticationError("LinkedIn API returned 401 Unauthorized.")

            case 403:
                body = res.json()
                logger.warning(f"SKIPPING profile – inaccessible/blocked/deleted → {profile_url}")
                logger.debug(f"Body: {json.dumps(body, indent=2)}")
                return None, None

        if not res.ok:
            body_str = res.body().decode("utf-8", errors="ignore") if isinstance(res.body(), bytes) else str(res.body())
            logger.error(f"Request failed with status {res.status}: {body_str}")
            raise Exception(f"LinkedIn API error {res.status}: {body_str}")

        data = res.json()
        extracted_info = parse_linkedin_voyager_response(data, public_identifier=public_identifier)
        return extracted_info, data
