# linkedin/navigation/utils.py
import logging
from urllib.parse import unquote, urlparse, urljoin

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from linkedin.conf import FIXTURE_PAGES_DIR
from linkedin.navigation.exceptions import SkipProfile

logger = logging.getLogger(__name__)


def goto_page(session: "AccountSession",
              action,
              expected_url_pattern: str,
              timeout: int = 10_000,
              error_message: str = "",
              ):
    from linkedin.db.crm_profiles import add_profile_urls
    page = session.page
    action()
    if not page:
        return

    try:
        page.wait_for_url(lambda url: expected_url_pattern in unquote(url), timeout=timeout)
    except PlaywrightTimeoutError:
        pass  # we still continue and check URL below

    session.wait()

    current = unquote(page.url)
    if expected_url_pattern not in current:
        if "/404" in current:
            raise SkipProfile(f"Profile returned 404 → {current}")
        raise RuntimeError(f"{error_message} → expected '{expected_url_pattern}' | got '{current}'")

    logger.debug("Navigated to %s", page.url)
    try:
        urls = _extract_in_urls(session)
        add_profile_urls(session, list(urls))
    except Exception as e:
        logger.error(f"Failed to extract/save profile URLs after navigation: {e}", exc_info=True)


def _extract_in_urls(session):
    from linkedin.db.crm_profiles import url_to_public_id

    page = session.page

    urls = set()
    for link in page.locator('a[href*="/in/"]').all():
        href = link.get_attribute("href")
        if href and "/in/" in href:
            full_url = urljoin(page.url, href.strip())
            clean = urlparse(full_url)._replace(query="", fragment="").geturl()
            try:
                url_to_public_id(clean)
            except ValueError:
                continue
            urls.add(clean)
    logger.debug(f"Extracted {len(urls)} unique /in/ profiles")
    return urls


def first_matching(page, selectors: list[str]):
    """Try selectors in order, return first locator that matches."""
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count() > 0:
            return locator.first
    return None


TOP_CARD_SELECTORS = [
    'section:has(div.top-card-background-hero-image)',
    'section[data-member-id]',
    'section.artdeco-card:has(> div.pv-top-card)',
    'section:has(> div[class*="pv-top-card"])',
    'section[componentkey*="com.linkedin.sdui.profile.card"]',
]


def get_top_card(session):
    top_card = first_matching(session.page, TOP_CARD_SELECTORS)
    if top_card is None:
        logger.warning("Top card not found on %s", session.page.url)
        raise SkipProfile("Top Card section not found")
    return top_card


def save_page(session: "AccountSession", profile: dict, ):
    filepath = FIXTURE_PAGES_DIR / f"{profile.get('public_identifier')}.html"
    html_content = session.page.content()
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_content)
    logger.info("Saved ambiguous connection status page → %s", filepath)
