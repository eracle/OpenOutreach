# linkedin/navigation/utils.py
import logging
from urllib.parse import unquote, urlparse, urljoin

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from linkedin.conf import FIXTURE_PAGES_DIR, OPPORTUNISTIC_SCRAPING
from linkedin.navigation.exceptions import SkipProfile

logger = logging.getLogger(__name__)


def goto_page(session: "AccountSession",
              action,
              expected_url_pattern: str,
              timeout: int = 10_000,
              error_message: str = "",
              to_scrape=True,
              ):
    from linkedin.db.profiles import add_profile_urls
    page = session.page
    action()
    if not page:
        return

    try:
        page.wait_for_url(lambda url: expected_url_pattern in unquote(url), timeout=timeout)
    except PlaywrightTimeoutError:
        pass  # we still continue and check URL below

    session.wait(to_scrape=to_scrape)

    current = unquote(page.url)
    if expected_url_pattern not in current:
        raise RuntimeError(f"{error_message} → expected '{expected_url_pattern}' | got '{current}'")

    logger.debug("Navigated to %s", page.url)
    if OPPORTUNISTIC_SCRAPING:
        try:
            urls = _extract_in_urls(session)
            add_profile_urls(session, list(urls))
        except Exception as e:
            logger.error(f"Failed to extract/save profile URLs after navigation: {e}", exc_info=True)


def _extract_in_urls(session):
    page = session.page
    urls = set()
    for link in page.locator('a[href*="/in/"]').all():
        href = link.get_attribute("href")
        if href and "/in/" in href:
            # resolves relative + protocol-relative URLs
            full_url = urljoin(page.url, href.strip())
            clean = urlparse(full_url)._replace(query="", fragment="").geturl()
            urls.add(clean)
    logger.debug(f"Extracted {len(urls)} unique /in/ profiles")
    return urls


def get_top_card(session):
    top_card = session.page.locator('section:has(div.top-card-background-hero-image)')

    if top_card.count() == 0:
        top_card = session.page.locator('section[data-member-id]')

    if top_card.count() == 0:
        top_card = session.page.locator('section.artdeco-card:has(> div.pv-top-card)')

    if top_card.count() == 0:
        top_card = session.page.locator('section[data-member-id] >> div.pv-top-card').locator('..')

    if top_card.count() == 0:
        top_card = session.page.locator('section:has(> div[class*="pv-top-card"])')

    if top_card.count() == 0:
        top_card = session.page.locator('section[componentkey*="com.linkedin.sdui.profile.card"]')

    if top_card.count() == 0:
        logger.info("Skipping profile")
        raise SkipProfile("Top Card section not found")

    return top_card.first  # there’s always only one


def save_page(session: "AccountSession", profile: dict, ):
    filepath = FIXTURE_PAGES_DIR / f"{profile.get('public_identifier')}.html"
    html_content = session.page.content()
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_content)
    logger.info("Saved ambiguous connection status page → %s", filepath)
