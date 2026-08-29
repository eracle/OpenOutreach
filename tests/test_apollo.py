# tests/test_apollo.py
"""Apollo slice — mock at the HTTP boundary (``requests.Session.post``).

Apollo is the *synchronous* finder: ``start`` returns a terminated ``Lookup`` and there
is no handle to poll. The cases worth pinning are the ones that cost money or put a bad
address in the export — a locked placeholder and a guessed address must both read as
misses, because either one exported as a hit is a bounce the operator paid for.
"""
from unittest.mock import MagicMock, patch

import pytest
import requests

from openoutreach.core.errors import ErrorType
from openoutreach.enrichment import apollo
from openoutreach.enrichment.apollo import ApolloUnavailable

URL = "https://www.linkedin.com/in/alice/"


@pytest.fixture
def keyed(db):
    from openoutreach.core.models import SiteConfig
    cfg = SiteConfig.load()
    cfg.apollo_api_key = "secret"
    cfg.save()
    return cfg


@pytest.fixture
def unkeyed(db):
    from openoutreach.core.models import SiteConfig
    cfg = SiteConfig.load()
    cfg.apollo_api_key = ""
    cfg.save()
    return cfg


def _response(payload, status=200):
    response = MagicMock(spec=requests.Response)
    response.ok = 200 <= status < 300
    response.status_code = status
    response.json.return_value = payload
    return response


def _person(**overrides):
    person = {
        "email": "alice@acme.com",
        "email_status": "verified",
        "first_name": "Alice",
        "last_name": "Ng",
    }
    person.update(overrides)
    return {"person": person}


# ── start ─────────────────────────────────────────────────────────


class TestStart:

    def test_a_verified_address_terminates_as_a_hit(self, keyed):
        with patch.object(requests.Session, "post", return_value=_response(_person())):
            lookup = apollo.start(URL)

        assert not lookup.pending
        assert lookup.request_id == ""
        assert lookup.outcome.hit
        assert lookup.outcome.email == "alice@acme.com"

    def test_a_hit_carries_the_name_apollo_resolved(self, keyed):
        with patch.object(requests.Session, "post", return_value=_response(_person())):
            outcome = apollo.start(URL).outcome

        assert (outcome.first_name, outcome.last_name) == ("Alice", "Ng")

    def test_no_match_is_a_terminal_miss(self, keyed):
        with patch.object(requests.Session, "post", return_value=_response({"person": None})):
            outcome = apollo.start(URL).outcome

        assert outcome.miss
        assert outcome.email == ""

    def test_a_locked_placeholder_is_a_miss_not_an_address(self, keyed):
        """Apollo answers an unentitled account with a syntactically valid fake.

        Exported unchecked it looks exactly like a resolved lead, so this is the one
        case where trusting the ``email`` field ships bounces.
        """
        payload = _person(email="email_not_unlocked@domain.com")
        with patch.object(requests.Session, "post", return_value=_response(payload)):
            outcome = apollo.start(URL).outcome

        assert outcome.miss

    def test_a_guessed_address_is_a_miss(self, keyed):
        """A guess is a pattern applied to a domain, with no delivery evidence."""
        payload = _person(email_status="guessed")
        with patch.object(requests.Session, "post", return_value=_response(payload)):
            outcome = apollo.start(URL).outcome

        assert outcome.miss

    def test_only_the_profile_url_is_sent(self, keyed):
        with patch.object(requests.Session, "post",
                          return_value=_response(_person())) as post:
            apollo.start(URL)

        assert post.call_args.kwargs["json"] == {"linkedin_url": URL}

    def test_reveal_flags_are_never_set(self, keyed):
        """Personal email and mobile cost extra credits and force webhook delivery."""
        with patch.object(requests.Session, "post",
                          return_value=_response(_person())) as post:
            apollo.start(URL)

        body = post.call_args.kwargs["json"]
        assert "reveal_personal_emails" not in body
        assert "reveal_phone_number" not in body
        assert "run_waterfall_email" not in body


# ── failure modes ─────────────────────────────────────────────────


class TestFailureModes:

    def test_no_key_raises_with_the_credential_error_type(self, unkeyed):
        with pytest.raises(ApolloUnavailable) as exc:
            apollo.start(URL)

        assert exc.value.error_type == ErrorType.NO_CREDENTIAL

    def test_a_403_reads_as_auth_not_as_an_empty_result(self, keyed):
        """The plan-tier refusal. Reported as no-leads-found it would look like the
        product does not work, which is the failure ``core/errors.py`` exists to stop."""
        with patch.object(requests.Session, "post", return_value=_response({}, status=403)):
            with pytest.raises(ApolloUnavailable) as exc:
                apollo.start(URL)

        assert exc.value.error_type == ErrorType.PROVIDER_AUTH

    def test_an_empty_wallet_is_its_own_error_type(self, keyed):
        with patch.object(requests.Session, "post", return_value=_response({}, status=402)):
            with pytest.raises(ApolloUnavailable) as exc:
                apollo.start(URL)

        assert exc.value.error_type == ErrorType.PROVIDER_OUT_OF_CREDITS

    def test_an_unreachable_service_is_provider_unavailable(self, keyed):
        with patch.object(requests.Session, "post",
                          side_effect=requests.ConnectionError("boom")):
            with pytest.raises(ApolloUnavailable) as exc:
                apollo.start(URL)

        assert exc.value.error_type == ErrorType.PROVIDER_UNAVAILABLE

    def test_polling_apollo_is_refused_loudly(self, keyed):
        """There is no job to poll. Silence here would strand a deal in FINDING_EMAIL."""
        with pytest.raises(ApolloUnavailable):
            apollo.poll_once("req1")


# ── credit balance ────────────────────────────────────────────────


class TestCreditBalance:

    def test_the_day_window_for_the_match_endpoint_is_the_balance(self, keyed):
        payload = {
            "['api/v1/people/match', 'match']": {
                "minute": {"limit": 50, "consumed": 1, "left_over": 49},
                "day": {"limit": 600, "consumed": 100, "left_over": 500},
            },
        }
        with patch.object(requests.Session, "post", return_value=_response(payload)):
            assert apollo.credit_balance() == 500

    def test_an_unreadable_payload_raises_rather_than_reporting_zero(self, keyed):
        """A balance we could not read is not a balance of zero — the difference
        decides whether the operator is told to top up."""
        with patch.object(requests.Session, "post", return_value=_response({})):
            with pytest.raises(ApolloUnavailable):
                apollo.credit_balance()
