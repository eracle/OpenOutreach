# tests/emails/test_bettercontact.py
"""BetterContact slice — mock at the HTTP boundary (`bettercontact._session`).

The paid finder is a two-leg async handshake: ``submit`` fires a job and returns
its ``request_id``; ``poll_once`` checks that job exactly once and reports
running / hit / miss. A missing key or an unreachable service raises
BetterContactUnavailable rather than a bare error.
"""
from unittest.mock import MagicMock, patch

import pytest
import requests

from openoutreach.emails import bettercontact
from openoutreach.emails.bettercontact import (
    BetterContactQuery,
    BetterContactUnavailable,
)

QUERY = BetterContactQuery(linkedin_url="https://www.linkedin.com/in/alice/")


@pytest.fixture
def keyed(db):
    from openoutreach.core.models import SiteConfig
    cfg = SiteConfig.load()
    cfg.bettercontact_api_key = "secret"
    cfg.save()
    return cfg


@pytest.fixture
def unkeyed(db):
    from openoutreach.core.models import SiteConfig
    cfg = SiteConfig.load()
    cfg.bettercontact_api_key = ""
    cfg.save()
    return cfg


def _response(body, error=None):
    resp = MagicMock()
    resp.json.return_value = body
    resp.raise_for_status.side_effect = error
    return resp


def _fake_session(post=None, get=None):
    """A requests.Session stand-in usable as a context manager."""
    session = MagicMock()
    session.__enter__.return_value = session
    session.post = post or MagicMock()
    session.get = get or MagicMock()
    return session


def _patch_session(post=None, get=None):
    return patch.object(bettercontact, "_session", return_value=_fake_session(post, get))


def _terminal(email, status):
    return _response({
        "status": "terminated",
        "data": [{"contact_email_address": email, "contact_email_address_status": status}],
    })


# ── bettercontact.submit ──────────────────────────────────────────────

class TestSubmit:
    def test_returns_request_id(self, keyed):
        post = MagicMock(return_value=_response({"id": "req1"}))
        with _patch_session(post):
            assert bettercontact.submit(QUERY) == "req1"

    def test_no_key_is_unavailable(self, unkeyed):
        post = MagicMock()
        with _patch_session(post), pytest.raises(BetterContactUnavailable):
            bettercontact.submit(QUERY)
        post.assert_not_called()

    def test_missing_request_id_is_unavailable(self, keyed):
        post = MagicMock(return_value=_response({}))  # no "id"/"request_id"
        with _patch_session(post), pytest.raises(BetterContactUnavailable):
            bettercontact.submit(QUERY)

    def test_http_error_is_unavailable(self, keyed):
        post = MagicMock(return_value=_response({}, error=requests.HTTPError("403")))
        with _patch_session(post), pytest.raises(BetterContactUnavailable):
            bettercontact.submit(QUERY)

    def test_network_error_is_unavailable(self, keyed):
        post = MagicMock(side_effect=requests.ConnectionError("boom"))
        with _patch_session(post), pytest.raises(BetterContactUnavailable):
            bettercontact.submit(QUERY)


# ── bettercontact.poll_once ───────────────────────────────────────────

class TestPollOnce:
    def test_running(self, keyed):
        get = MagicMock(return_value=_response({"status": "in progress"}))
        with _patch_session(get=get):
            outcome = bettercontact.poll_once("req1")
        assert outcome.running and not outcome.hit and not outcome.miss
        get.assert_called_once()  # a single poll, no retry loop

    def test_hit(self, keyed):
        get = MagicMock(return_value=_terminal("alice@acme.com", "valid"))
        with _patch_session(get=get):
            outcome = bettercontact.poll_once("req1")
        assert outcome.hit and outcome.email == "alice@acme.com"

    def test_terminal_no_usable_email_is_miss(self, keyed):
        get = MagicMock(return_value=_terminal(None, "not_found"))
        with _patch_session(get=get):
            outcome = bettercontact.poll_once("req1")
        assert outcome.miss and not outcome.hit

    def test_no_key_is_unavailable(self, unkeyed):
        get = MagicMock()
        with _patch_session(get=get), pytest.raises(BetterContactUnavailable):
            bettercontact.poll_once("req1")
        get.assert_not_called()

    def test_transport_error_is_unavailable(self, keyed):
        get = MagicMock(return_value=_response({}, error=requests.HTTPError("500")))
        with _patch_session(get=get), pytest.raises(BetterContactUnavailable):
            bettercontact.poll_once("req1")


# ── bettercontact.is_configured ───────────────────────────────────────

class TestIsConfigured:
    def test_false_when_key_blank(self, unkeyed):
        assert bettercontact.is_configured() is False

    def test_true_when_key_set(self, keyed):
        assert bettercontact.is_configured() is True
