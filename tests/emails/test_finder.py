# tests/emails/test_finder.py
"""Finder slice — mock at the HTTP boundary (`bettercontact._session`).

The finder is tri-state: a FinderResult (hit), None (finder ran, found
nothing), or a raised FinderUnavailable (no key / service unreachable).
"""
from unittest.mock import MagicMock, patch

import pytest
import requests

from openoutreach.emails import bettercontact, finder
from openoutreach.emails.finder import FinderQuery, FinderResult, FinderUnavailable

QUERY = FinderQuery(linkedin_url="https://www.linkedin.com/in/alice/")


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


# ── bettercontact.find_email ──────────────────────────────────────────

class TestFindEmail:
    def test_usable_hit_returns_result(self):
        post = MagicMock(return_value=_response({"id": "req1"}))
        get = MagicMock(return_value=_terminal("alice@acme.com", "valid"))
        with _patch_session(post, get):
            result = bettercontact.find_email("key", QUERY)
        assert result == FinderResult(email="alice@acme.com", status="valid")

    def test_not_found_is_a_miss(self):
        post = MagicMock(return_value=_response({"id": "req1"}))
        get = MagicMock(return_value=_terminal(None, "not_found"))
        with _patch_session(post, get):
            assert bettercontact.find_email("key", QUERY) is None

    def test_polls_until_terminal(self):
        post = MagicMock(return_value=_response({"id": "req1"}))
        get = MagicMock(side_effect=[
            _response({"status": "in progress"}),
            _terminal("alice@acme.com", "catch_all_safe"),
        ])
        with _patch_session(post, get), patch.object(bettercontact.time, "sleep"):
            result = bettercontact.find_email("key", QUERY)
        assert result == FinderResult(email="alice@acme.com", status="catch_all_safe")
        assert get.call_count == 2

    def test_submit_http_error_is_unavailable(self):
        post = MagicMock(return_value=_response({}, error=requests.HTTPError("403")))
        get = MagicMock()
        with _patch_session(post, get), pytest.raises(FinderUnavailable):
            bettercontact.find_email("key", QUERY)
        get.assert_not_called()

    def test_missing_request_id_is_unavailable(self):
        post = MagicMock(return_value=_response({}))  # no "id"
        get = MagicMock()
        with _patch_session(post, get), pytest.raises(FinderUnavailable):
            bettercontact.find_email("key", QUERY)
        get.assert_not_called()

    def test_poll_timeout_is_unavailable(self):
        post = MagicMock(return_value=_response({"id": "req1"}))
        get = MagicMock(return_value=_response({"status": "in progress"}))
        clock = (t for t in [0.0] + [1e9] * 100)
        with _patch_session(post, get), \
                patch.object(bettercontact.time, "sleep"), \
                patch.object(bettercontact.time, "monotonic", side_effect=clock), \
                pytest.raises(FinderUnavailable):
            bettercontact.find_email("key", QUERY)

    def test_network_error_is_unavailable(self):
        post = MagicMock(side_effect=requests.ConnectionError("boom"))
        with _patch_session(post), pytest.raises(FinderUnavailable):
            bettercontact.find_email("key", QUERY)


# ── finder.resolve_email (SiteConfig gate) ────────────────────────────

class TestResolveEmail:
    def test_no_key_is_unavailable(self):
        from openoutreach.core.models import SiteConfig
        cfg = SiteConfig.load()
        cfg.finder_api_key = ""
        cfg.save()
        with patch.object(bettercontact, "find_email") as find_email:
            with pytest.raises(FinderUnavailable):
                finder.resolve_email(QUERY)
        find_email.assert_not_called()

    def test_with_key_delegates_to_provider(self):
        from openoutreach.core.models import SiteConfig
        cfg = SiteConfig.load()
        cfg.finder_api_key = "secret"
        cfg.save()
        sentinel = FinderResult(email="alice@acme.com", status="valid")
        with patch.object(bettercontact, "find_email", return_value=sentinel) as find_email:
            assert finder.resolve_email(QUERY) is sentinel
        find_email.assert_called_once_with("secret", QUERY)
