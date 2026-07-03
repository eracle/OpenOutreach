# tests/contacts/test_service.py
"""Contacts store client — mock at the HTTP boundary (``service.requests``).

Two best-effort calls: ``resolve`` (ask the hub before paying BetterContact) and
``contribute`` (give back what we find, non-EU only, registering on first use).
"""
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import requests

from openoutreach.contacts import service
from openoutreach.core.models import SiteConfig
from tests.factories import LeadFactory


def _resp(status_code=200, body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body or {}
    resp.raise_for_status.side_effect = (
        None if status_code < 400 else requests.HTTPError(str(status_code))
    )
    return resp


def _config(token="tok", url="", country_code="us"):
    # country_code is the operator's jurisdiction — the give-back gate. Default
    # non-EEA so contribute proceeds; the EEA-operator test overrides it.
    cfg = SiteConfig.load()
    cfg.contacts_api_token = token
    cfg.contacts_api_url = url
    cfg.country_code = country_code
    cfg.save()
    return cfg


def _session(contribute_to_hub=True):
    """A daemon session stand-in for the register path."""
    session = MagicMock()
    session.self_profile = {"public_identifier": "me"}
    session.django_user.email = "me@x.com"
    session.linkedin_profile.linkedin_username = "me-user"
    session.linkedin_profile.contribute_to_hub = contribute_to_hub
    return session


# ── resolve ──────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestResolve:
    def test_no_token_returns_none_without_a_call(self):
        _config(token="")
        lead = LeadFactory(profile_url="jane-doe")
        with patch.object(service.requests, "get") as get:
            assert service.resolve(lead) is None
        get.assert_not_called()

    def test_hit_returns_email(self):
        _config()
        lead = LeadFactory(profile_url="jane-doe")
        body = {"public_identifier": "jane-doe", "emails": ["jane@acme.com"]}
        with patch.object(service.requests, "get", return_value=_resp(200, body)):
            assert service.resolve(lead) == "jane@acme.com"

    def test_hit_with_multiple_emails_takes_first(self):
        _config()
        lead = LeadFactory(profile_url="jane-doe")
        body = {"public_identifier": "jane-doe", "emails": ["jane@acme.com", "j@personal.com"]}
        with patch.object(service.requests, "get", return_value=_resp(200, body)):
            assert service.resolve(lead) == "jane@acme.com"

    def test_hit_with_empty_emails_returns_none(self):
        _config()
        lead = LeadFactory(profile_url="jane-doe")
        with patch.object(service.requests, "get", return_value=_resp(200, {"emails": []})):
            assert service.resolve(lead) is None

    def test_miss_returns_none(self):
        _config()
        lead = LeadFactory()
        with patch.object(service.requests, "get", return_value=_resp(404, {})):
            assert service.resolve(lead) is None

    def test_outage_returns_none(self):
        _config()
        lead = LeadFactory()
        with patch.object(
            service.requests, "get", side_effect=requests.ConnectionError("boom"),
        ):
            assert service.resolve(lead) is None


# ── contribute ───────────────────────────────────────────────────────


@pytest.mark.django_db
class TestContribute:
    def test_empty_emails_is_a_noop(self):
        _config()
        lead = LeadFactory(country_code="in")
        with patch.object(service.requests, "post") as post:
            service.contribute(_session(), lead, [], service.ORIGIN_BETTERCONTACT)
        post.assert_not_called()

    def test_eea_lead_is_skipped_client_side(self):
        _config()
        lead = LeadFactory(country_code="de")
        with patch.object(service.requests, "post") as post:
            service.contribute(_session(), lead, ["jane@acme.com"], service.ORIGIN_BETTERCONTACT)
        post.assert_not_called()

    def test_unknown_country_is_skipped(self):
        _config()
        lead = LeadFactory(country_code="")
        with patch.object(service.requests, "post") as post:
            service.contribute(_session(), lead, ["jane@acme.com"], service.ORIGIN_BETTERCONTACT)
        post.assert_not_called()

    def test_with_token_posts_the_record(self):
        _config(token="tok")
        lead = LeadFactory(profile_url="jane-doe", country_code="in")
        with patch.object(
            service.requests, "post", return_value=_resp(200, {"accepted": 1, "credits": 7}),
        ) as post:
            # the empty string is filtered out
            service.contribute(_session(), lead, ["jane@acme.com", ""], service.ORIGIN_PROFILE_INFO)
        url, kwargs = post.call_args.args[0], post.call_args.kwargs
        assert url.endswith("/api/contribute/")
        assert kwargs["headers"] == {"Authorization": "Bearer tok"}
        assert kwargs["json"] == {
            "public_identifier": "jane-doe",
            "country_code": "in",
            "emails": ["jane@acme.com"],
            "origin": "profile_info",
        }

    def test_first_contribution_registers_and_persists_token(self):
        _config(token="")
        lead = LeadFactory(profile_url="jane-doe", country_code="br")
        with patch.object(
            service.requests, "post", return_value=_resp(200, {"token": "NEW", "credits": 1}),
        ) as post:
            service.contribute(_session(), lead, ["jane@acme.com"], service.ORIGIN_BETTERCONTACT)
        url, kwargs = post.call_args.args[0], post.call_args.kwargs
        assert url.endswith("/api/register/")
        assert kwargs["json"]["linkedin_public_id"] == "me"
        assert kwargs["json"]["subscriber_email"] == "me@x.com"
        assert kwargs["json"]["origin"] == "bettercontact"  # origin rides the folded register
        assert SiteConfig.load().contacts_api_token == "NEW"

    def test_outage_is_swallowed_and_no_token_stored(self):
        _config(token="")
        lead = LeadFactory(country_code="in")
        with patch.object(
            service.requests, "post", side_effect=requests.ConnectionError("boom"),
        ):
            # must not raise
            service.contribute(_session(), lead, ["jane@acme.com"], service.ORIGIN_BETTERCONTACT)
        assert SiteConfig.load().contacts_api_token == ""

    def test_eea_operator_contributes_nothing(self):
        """An operator inside the EEA/UK/CH does not give back (jurisdiction gate)."""
        cfg = _config(token="tok")
        cfg.country_code = "de"
        cfg.save()
        lead = LeadFactory(country_code="in")
        with patch.object(service.requests, "post") as post:
            service.contribute(_session(), lead, ["jane@acme.com"], service.ORIGIN_BETTERCONTACT)
        post.assert_not_called()

    def test_cached_embedding_rides_along(self):
        _config(token="tok")
        lead = LeadFactory(profile_url="jane-doe", country_code="in")
        lead.embedding_array = np.arange(384, dtype=np.float32)
        with patch.object(
            service.requests, "post", return_value=_resp(200, {"accepted": 1, "credits": 7}),
        ) as post:
            service.contribute(_session(), lead, ["jane@acme.com"], service.ORIGIN_BETTERCONTACT)
        assert post.call_args.kwargs["json"]["embedding"] == list(range(384))

    def test_uncached_embedding_is_omitted(self):
        _config(token="tok")
        lead = LeadFactory(country_code="in")  # no embedding cached
        with patch.object(
            service.requests, "post", return_value=_resp(200, {"accepted": 1, "credits": 7}),
        ) as post:
            service.contribute(_session(), lead, ["jane@acme.com"], service.ORIGIN_BETTERCONTACT)
        assert "embedding" not in post.call_args.kwargs["json"]
