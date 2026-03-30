# tests/browser/test_connect_selectors.py
"""
Regression tests for connect/status selectors against real LinkedIn page snapshots.

Fixtures are sanitized HTML fragments extracted from actual LinkedIn profile pages.
To add a new regression case: save a snapshot via dump_page_html(), sanitize PII,
and place it in tests/fixtures/pages/.
"""
import pytest

from linkedin.actions.connect import SELECTORS as CONNECT_SELECTORS
from linkedin.browser.nav import TOP_CARD_SELECTORS
from tests.browser.conftest import load_fixture


# -- helpers ------------------------------------------------------------------

def find_top_card(page):
    for selector in TOP_CARD_SELECTORS:
        loc = page.locator(selector)
        if loc.count() > 0:
            return loc.first
    return None


# -- fixtures -----------------------------------------------------------------

CONNECTED_FIXTURE = "771_connected_profile.html"
CONNECT_FIXTURE = "771_connect_profile.html"


@pytest.fixture
def connected_page(page):
    """Profile page where the viewer is already connected (shows 'Message')."""
    return load_fixture(page, CONNECTED_FIXTURE)


@pytest.fixture
def connect_page(page):
    """Profile page where the viewer is NOT connected (shows 'Connect')."""
    return load_fixture(page, CONNECT_FIXTURE)


# -- top card detection -------------------------------------------------------

class TestTopCard:
    def test_found_on_connected_page(self, connected_page):
        assert find_top_card(connected_page) is not None

    def test_found_on_connect_page(self, connect_page):
        assert find_top_card(connect_page) is not None



# -- connect profile: button detection ----------------------------------------

class TestConnectButton:
    """A profile showing 'Connect' should be actionable by the connect flow."""

    def test_connect_text_in_top_card(self, connect_page):
        top_card = find_top_card(connect_page)
        text = top_card.inner_text()
        assert "Connect" in text

    def test_more_button_found(self, connect_page):
        top_card = find_top_card(connect_page)
        loc = top_card.locator(CONNECT_SELECTORS["more_button"])
        assert loc.count() > 0

    def test_invite_to_connect_selector(self, connect_page):
        top_card = find_top_card(connect_page)
        loc = top_card.locator(CONNECT_SELECTORS["invite_to_connect"])
        assert loc.count() > 0
