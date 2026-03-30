# tests/browser/test_connect_selectors.py
"""
Regression tests for connect/status selectors against real LinkedIn page snapshots.

Pages saved by dump_page_html() land in category subdirectories under
tests/fixtures/pages/ (e.g. pages/connect/).  Parametrized tests auto-discover
every file in each subdirectory so new dumps are tested without manual setup.
"""
import pytest

from linkedin.actions.connect import SELECTORS as CONNECT_SELECTORS
from linkedin.actions.status import SELECTORS as STATUS_SELECTORS
from linkedin.browser.nav import TOP_CARD_SELECTORS
from linkedin.conf import FIXTURE_PAGES_DIR
from tests.browser.conftest import load_fixture


# -- helpers ------------------------------------------------------------------

def find_top_card(page):
    for selector in TOP_CARD_SELECTORS:
        loc = page.locator(selector)
        if loc.count() > 0:
            return loc.first
    return None


# -- hand-crafted fixtures (root level) ---------------------------------------

CONNECTED_FIXTURE = "771_connected_profile.html"
CONNECT_FIXTURE = "771_connect_profile.html"


@pytest.fixture
def connected_page(page):
    return load_fixture(page, CONNECTED_FIXTURE)


@pytest.fixture
def connect_page(page):
    return load_fixture(page, CONNECT_FIXTURE)


class TestTopCard:
    def test_found_on_connected_page(self, connected_page):
        assert find_top_card(connected_page) is not None

    def test_found_on_connect_page(self, connect_page):
        assert find_top_card(connect_page) is not None


class TestConnectButton:
    """A profile showing 'Connect' should be actionable by the connect flow."""

    def test_connect_text_in_top_card(self, connect_page):
        top_card = find_top_card(connect_page)
        assert "Connect" in top_card.inner_text()

    def test_more_button_found(self, connect_page):
        top_card = find_top_card(connect_page)
        assert top_card.locator(CONNECT_SELECTORS["more_button"]).count() > 0

    def test_invite_to_connect_selector(self, connect_page):
        top_card = find_top_card(connect_page)
        assert top_card.locator(CONNECT_SELECTORS["invite_to_connect"]).count() > 0


# -- auto-discovered: pages/connect/ ------------------------------------------
# Every page dumped when the connect button wasn't found.
# The fix should make the selector match, so we assert it does.

CONNECT_DUMPS = sorted(
    p.name for p in (FIXTURE_PAGES_DIR / "connect").glob("*.html")
) if (FIXTURE_PAGES_DIR / "connect").exists() else []


@pytest.mark.parametrize("fixture", CONNECT_DUMPS)
def test_connect_dump_has_connect_or_more(page, fixture):
    pg = load_fixture(page, "connect", fixture)
    top_card = find_top_card(pg)
    assert top_card is not None, f"connect/{fixture}: no top card"
    connect = top_card.locator(CONNECT_SELECTORS["invite_to_connect"]).count()
    more = top_card.locator(CONNECT_SELECTORS["more_button"]).count()
    assert connect or more, f"connect/{fixture}: no Connect or More button"


# -- auto-discovered: pages/pending/ ------------------------------------------
# Pages where the UI shows "Pending" despite the API reporting degree 2/3.

PENDING_DUMPS = sorted(
    p.name for p in (FIXTURE_PAGES_DIR / "pending").glob("*.html")
) if (FIXTURE_PAGES_DIR / "pending").exists() else []


@pytest.mark.parametrize("fixture", PENDING_DUMPS)
def test_pending_dump_has_pending_button(page, fixture):
    pg = load_fixture(page, "pending", fixture)
    top_card = find_top_card(pg)
    assert top_card is not None, f"pending/{fixture}: no top card"
    pending = top_card.locator(STATUS_SELECTORS["pending_button"]).count()
    assert pending, f"pending/{fixture}: no Pending button"
