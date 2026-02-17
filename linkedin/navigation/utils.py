# linkedin/navigation/utils.py
import logging
import random
import time
from urllib.parse import unquote, urlparse, urljoin

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from linkedin.conf import CAMPAIGN_CONFIG, FIXTURE_PAGES_DIR
from linkedin.navigation.exceptions import SkipProfile

logger = logging.getLogger(__name__)


def goto_page(session: "AccountSession",
              action,
              expected_url_pattern: str,
              timeout: int = 10_000,
              error_message: str = "",
              ):
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
    urls = _extract_in_urls(session)
    _enrich_new_urls(session, urls)


def _extract_in_urls(session):
    from linkedin.db.crm_profiles import url_to_public_id

    page = session.page

    urls = set()
    for link in page.locator('a[href*="/in/"]').all():
        href = link.get_attribute("href")
        if href and "/in/" in href:
            full_url = urljoin(page.url, href.strip())
            clean = urlparse(full_url)._replace(query="", fragment="").geturl()
            if not url_to_public_id(clean):
                continue
            urls.add(clean)
    logger.debug(f"Extracted {len(urls)} unique /in/ profiles")
    return urls


def _enrich_new_urls(session, urls: set):
    """For each new URL, call Voyager API, create enriched Lead, embed.

    Skips URLs that already have a Lead. Rate-limits with enrich_min_interval.
    NO pre-existing connection check — handled by connect lane.
    """
    from linkedin.db.crm_profiles import lead_exists, create_enriched_lead, url_to_public_id
    from linkedin.ml.embeddings import embed_profile

    new_urls = [u for u in urls if not lead_exists(u)]
    if not new_urls:
        return

    logger.info("Discovered %d new profiles (%d total on page)", len(new_urls), len(urls))

    min_interval = CAMPAIGN_CONFIG.get("enrich_min_interval", 1)
    api = None
    enriched = 0

    for url in new_urls:
        public_id = url_to_public_id(url)
        if not public_id:
            continue

        if api is None:
            from linkedin.api.client import PlaywrightLinkedinAPI
            session.ensure_browser()
            api = PlaywrightLinkedinAPI(session=session)

        try:
            profile, data = api.get_profile(profile_url=url)
        except Exception:
            logger.warning("Voyager API failed for %s — skipping", url)
            continue

        if not profile:
            logger.warning("Empty profile for %s — skipping", url)
            continue

        lead_pk = create_enriched_lead(session, url, profile, data)
        if lead_pk is not None:
            embed_profile(lead_pk, public_id, profile)
            enriched += 1

        time.sleep(min_interval)

    logger.info("Enriched %d/%d new profiles", enriched, len(new_urls))


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


def human_type(locator, text: str, min_delay: int = 50, max_delay: int = 200):
    """Type text with randomized per-keystroke delay to mimic human input."""
    locator.type(text, delay=random.randint(min_delay, max_delay))


def save_page(session: "AccountSession", profile: dict, ):
    filepath = FIXTURE_PAGES_DIR / f"{profile.get('public_identifier')}.html"
    html_content = session.page.content()
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_content)
    logger.info("Saved ambiguous connection status page → %s", filepath)
