# linkedin/api/client.py
import json
import logging
from typing import Optional, Any
from urllib.parse import urlencode

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from linkedin.api.voyager import parse_linkedin_voyager_response, parse_connection_degree
from linkedin.url_utils import url_to_public_id
from linkedin.exceptions import AuthenticationError, ProfileInaccessibleError

logger = logging.getLogger(__name__)


class _FetchResponse:
    """Thin wrapper around the dict returned by page.evaluate(fetch(...))."""

    __slots__ = ("status", "ok", "_text")

    def __init__(self, raw: dict):
        self.status: int = raw["status"]
        self.ok: bool = raw["ok"]
        self._text: str = raw["body"]

    def json(self) -> Any:
        return json.loads(self._text)

    def text(self) -> str:
        return self._text


VOYAGER_REQUEST_TIMEOUT_MS = 30_000


class PlaywrightLinkedinAPI:

    def __init__(
            self,
            session: "AccountSession",
            timeout_ms: int = VOYAGER_REQUEST_TIMEOUT_MS,
    ):
        self.session = session
        self.page = session.page
        self.context = session.context
        self.timeout_ms = timeout_ms

        # Extract cookies from the browser context to get JSESSIONID for csrf-token
        cookies = self.context.cookies()
        cookies_dict = {c['name']: c['value'] for c in cookies}
        jsessionid = cookies_dict.get('JSESSIONID', '').strip('"')

        # Only API-level headers; fetch() inside the page inherits
        # browser-injected headers (x-li-track, sec-ch-*, user-agent, …).
        self.headers = {
            'accept': 'application/vnd.linkedin.normalized+json+2.1',
            'csrf-token': jsessionid,
            'x-li-lang': 'en_US',
            'x-restli-protocol-version': '2.0.0',
        }

    # -- transport --------------------------------------------------------

    def _fetch(self, method: str, url: str, headers: dict,
               body: str | None = None) -> _FetchResponse:
        """Run fetch() inside the browser page context.

        This ensures the request carries all browser-injected headers
        (x-li-track, cookies, sec-ch-*, …) exactly like a real XHR.
        """
        raw = self.page.evaluate(
            """([method, url, headers, body, timeoutMs]) => {
                const controller = new AbortController();
                const timer = setTimeout(() => controller.abort(), timeoutMs);
                const init = {method, headers, credentials: "include",
                              signal: controller.signal};
                if (body !== null) init.body = body;
                return fetch(url, init).then(async r => {
                    clearTimeout(timer);
                    return {status: r.status, ok: r.ok, body: await r.text()};
                });
            }""",
            [method, url, headers, body, self.timeout_ms],
        )
        return _FetchResponse(raw)

    def get(self, url: str, *, headers: dict | None = None,
            params: dict | None = None) -> _FetchResponse:
        h = {**self.headers, **(headers or {})}
        if params:
            url = f"{url}?{urlencode(params)}"
        return self._fetch("GET", url, h)

    def post(self, url: str, *, headers: dict | None = None,
             data: str | None = None) -> _FetchResponse:
        h = {**self.headers, **(headers or {})}
        return self._fetch("POST", url, h, body=data)

    def _check_profile_response(self, res: _FetchResponse, public_identifier: str) -> None:
        """Raise on auth/access errors; pass through on success."""
        if res.status == 401:
            logger.error("LinkedIn API → 401 Unauthorized (session expired or blocked)")
            raise AuthenticationError("LinkedIn API returned 401 Unauthorized.")
        if res.status in (403, 404):
            logger.info("Profile inaccessible → private / deleted / restricted → %s (HTTP %d)",
                        public_identifier, res.status)
            raise ProfileInaccessibleError(f"{public_identifier} (HTTP {res.status})")
        if not res.ok:
            body_str = res.text()
            logger.error("API request failed → %s | Status: %s", public_identifier, res.status)
            raise IOError(f"LinkedIn API error {res.status}: {body_str[:500]}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type(IOError),
        reraise=True,
    )
    def get_profile(
            self, public_identifier: Optional[str] = None, profile_url: Optional[str] = None
    ) -> tuple[None, None] | tuple[dict, Any]:
        if not public_identifier and profile_url:
            public_identifier = url_to_public_id(profile_url)

        if not public_identifier:  # None from url_to_public_id or missing arg
            raise ValueError("Need public_identifier or profile_url")

        params = {
            'decorationId': 'com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-91',
            'memberIdentity': public_identifier,
            'q': 'memberIdentity',
        }

        base_url = "https://www.linkedin.com/voyager/api"
        uri = "/identity/dash/profiles"
        full_url = base_url + uri

        res = self.get(full_url, params=params)

        self._check_profile_response(res, public_identifier)

        data = res.json()
        extracted_info = parse_linkedin_voyager_response(data, public_identifier=public_identifier)
        return extracted_info, data

    TOPCARD_DECORATION = (
        "com.linkedin.voyager.dash.deco.identity.profile.TopCardSupplementary-120"
    )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type(IOError),
        reraise=True,
    )
    def get_connection_degree(self, public_identifier: str) -> int | None:
        """Fetch connection degree via the TopCard decoration.

        Uses a lightweight decoration that reliably includes
        MemberRelationship entities even when FullProfileWithEntities
        does not.  Returns 1/2/3 or None.
        """
        res = self.get(
            "https://www.linkedin.com/voyager/api/identity/dash/profiles",
            params={
                "decorationId": self.TOPCARD_DECORATION,
                "memberIdentity": public_identifier,
                "q": "memberIdentity",
            },
        )

        self._check_profile_response(res, public_identifier)

        return parse_connection_degree(res.json())
