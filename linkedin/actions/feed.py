"""Scrape LinkedIn main feed for posts from connections using Playwright
browser navigation.

Returns structured post data: author name, post text, post URL.
"""
import logging
import re
from typing import Optional
from urllib.parse import urljoin

from linkedin.browser.nav import goto_page

logger = logging.getLogger(__name__)

FEED_URL = "https://www.linkedin.com/feed/"

# How many times to scroll to collect fresh posts
SCROLL_COUNT = 5
SCROLL_PAUSE_SECONDS = (2, 4)

POST_TEXT_SELECTORS = [
    'div[data-test-id="main-feed-activity-card"] div[class*="feed-shared-update-v2__description"]',
    'div[data-test-id="main-feed-activity-card"] div[class*="break-words"]',
    'div[data-test-id="main-feed-activity-card"] span[class*="visually-hidden"]',
    'article div.feed-shared-text',
]

POST_AUTHOR_SELECTORS = [
    'div[data-test-id="main-feed-activity-card"] span[class*="feed-shared-actor__name"]',
    'div[data-test-id="main-feed-activity-card"] a span[dir="auto"]',
    'div[data-test-id="main-feed-activity-card"] span[class*="hoverable-link-text"]',
]


def _scroll_feed(session) -> None:
    """Scroll the feed page to load more posts."""
    page = session.page
    for i in range(SCROLL_COUNT):
        page.evaluate("window.scrollBy(0, window.innerHeight)")
        session.wait(*SCROLL_PAUSE_SECONDS)
        logger.debug("Scrolled feed %d/%d", i + 1, SCROLL_COUNT)


def _extract_post_text(card) -> Optional[str]:
    """Try to extract post body text from a feed card."""
    for sel in POST_TEXT_SELECTORS:
        loc = card.locator(sel)
        if loc.count() > 0:
            text = loc.first.inner_text(timeout=3000).strip()
            if text:
                text = re.sub(r'\s+', ' ', text)
                return text
    return None


def _extract_author(card) -> Optional[str]:
    """Extract the author name from a feed card."""
    for sel in POST_AUTHOR_SELECTORS:
        loc = card.locator(sel)
        if loc.count() > 0:
            name = loc.first.inner_text(timeout=3000).strip()
            if name:
                return name
    return None


def _extract_post_urn(card) -> Optional[str]:
    """Extract the post URN from a feed card."""
    # Look for anchor tags with activity URNs in their href
    for link in card.locator('a[href*="/feed/update/urn:li:activity:"]').all():
        href = link.get_attribute("href") or ""
        m = re.search(r'urn:li:activity:\d+', href)
        if m:
            return m.group(0)
    return None


def _extract_post_url(card) -> Optional[str]:
    """Extract the post's permalink URL from a feed card."""
    for link in card.locator('a[href*="/feed/update/"]').all():
        href = link.get_attribute("href") or ""
        if "/feed/update/" in href:
            return urljoin("https://www.linkedin.com", href)
    return None


class FeedPost:
    """A single feed post from the LinkedIn home page."""

    __slots__ = ("author", "text", "url", "post_urn", "card_element")

    def __init__(self, author: str, text: str, url: Optional[str],
                 post_urn: Optional[str], card_element) -> None:
        self.author = author
        self.text = text
        self.url = url
        self.post_urn = post_urn
        self.card_element = card_element

    def __repr__(self) -> str:
        return f"FeedPost(author={self.author!r}, text={self.text[:60]}...)"


def scrape_feed(session, max_posts: int = 30) -> list[FeedPost]:
    """Navigate to the LinkedIn feed, scroll, and extract posts.

    Returns a list of ``FeedPost`` objects sorted by recency (top of feed
    first). Skips posts without text content.
    """
    session.ensure_browser()

    goto_page(
        session,
        action=lambda: session.page.goto(FEED_URL, wait_until="domcontentloaded"),
        expected_url_pattern="/feed/",
        error_message="Failed to navigate to LinkedIn feed",
    )
    session.wait(3, 5)

    _scroll_feed(session)

    cards = session.page.locator(
        'div[data-test-id="main-feed-activity-card"]'
    ).all()

    posts = []
    seen_texts = set()

    for card in cards:
        text = _extract_post_text(card)
        if not text:
            continue
        # Deduplicate by text prefix
        text_key = text[:120]
        if text_key in seen_texts:
            continue
        seen_texts.add(text_key)

        author = _extract_author(card) or "Unknown"
        url = _extract_post_url(card)
        post_urn = _extract_post_urn(card)

        posts.append(FeedPost(
            author=author, text=text, url=url,
            post_urn=post_urn, card_element=card,
        ))

        if len(posts) >= max_posts:
            break

    logger.info("Scraped %d feed posts", len(posts))
    return posts


if __name__ == "__main__":
    from linkedin.browser.registry import cli_parser, cli_session

    parser = cli_parser("Scrape LinkedIn feed posts")
    args = parser.parse_args()
    session = cli_session(args)

    posts = scrape_feed(session)
    for i, post in enumerate(posts, 1):
        print(f"\n--- Post {i} ---")
        print(f"Author: {post.author}")
        print(f"Text: {post.text[:200]}...")
        print(f"URL: {post.url}")
        print(f"URN: {post.post_urn}")

    input("\nPress Enter to close...")
    session.close()
