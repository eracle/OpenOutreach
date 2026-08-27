# tests/test_output_contract.py
"""The CLI's output contract — the part a program depends on.

Three promises: **stdout is result-only** (so a redirect yields data and nothing
else), **an expected failure is one typed line** on stderr with a non-zero exit (so
an agent branches instead of parsing prose), and **a 429 is backed off rather than
retried at speed** — their docs warn that a client which keeps firing can get the
account blocked.
"""
import io
import logging
import sys
from unittest.mock import MagicMock, patch

import pytest
import requests

from openoutreach.core.errors import ErrorType, OpenOutreachError, format_error
from openoutreach.core.management.base import OpenOutreachCommand
from openoutreach.enrichment import bettercontact
from openoutreach.enrichment.bettercontact import BetterContactUnavailable


# ── stdout is result-only ────────────────────────────────────────

def test_logs_go_to_stderr_not_stdout(capsys):
    """A log line on stdout would corrupt a redirected CSV or a piped JSON document."""
    from openoutreach.core.logging import configure_logging

    try:
        configure_logging(level=logging.INFO)
        logging.getLogger("openoutreach.test").info("a log line")
    finally:
        logging.getLogger().handlers.clear()

    captured = capsys.readouterr()
    assert "a log line" in captured.err
    assert captured.out == ""


def test_the_banner_is_decoration_and_shares_stderr(capsys):
    from openoutreach.core.logging import print_banner

    print_banner()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "___" in captured.err  # the ASCII banner


# ── one typed error line, non-zero exit ──────────────────────────

def test_error_line_has_the_contract_shape():
    assert format_error("provider_auth", "boom") == "error: provider_auth: boom"
    assert str(OpenOutreachError(ErrorType.PROVIDER_AUTH, "boom")) == \
        "error: provider_auth: boom"


def test_command_renders_the_error_line_and_exits_non_zero(capsys):
    """No traceback: a rejected key is an answer, not a bug."""
    class Failing(OpenOutreachCommand):
        def handle(self, *args, **options):
            raise OpenOutreachError(ErrorType.PROVIDER_AUTH, "the key was rejected")

    with pytest.raises(SystemExit) as exc:
        Failing().run_from_argv(["openoutreach", "failing"])

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == "error: provider_auth: the key was rejected"
    assert captured.out == ""


def test_json_callers_get_the_failure_as_json(capsys):
    """A caller that asked for JSON is parsing, not reading — the same vocabulary,
    in the shape it can consume."""
    import json

    class Failing(OpenOutreachCommand):
        def add_arguments(self, parser):
            parser.add_argument("--json", action="store_true", dest="as_json")

        def handle(self, *args, **options):
            raise OpenOutreachError(ErrorType.BAD_CONFIG, "several campaigns: 'A', 'B'")

    with pytest.raises(SystemExit) as exc:
        Failing().run_from_argv(["openoutreach", "failing", "--json"])

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert json.loads(captured.err) == {
        "error": {"type": "bad_config", "message": "several campaigns: 'A', 'B'"}}
    # Still stderr: stdout stays result-only, or `find --json > leads.json` would
    # collect an error object into the file the operator is keeping.
    assert captured.out == ""


def test_an_unexpected_exception_still_raises():
    """Only *expected* failures are flattened; a bug keeps its traceback."""
    class Buggy(OpenOutreachCommand):
        def handle(self, *args, **options):
            raise ZeroDivisionError("a real bug")

    with pytest.raises(ZeroDivisionError):
        Buggy().run_from_argv(["openoutreach", "buggy"])


# ── a fresh install is an answer, not a traceback ────────────────

def test_reading_an_unmigrated_database_is_a_typed_error(capsys):
    """`openoutreach status` before the first run used to raise `no such table`."""
    class Reader(OpenOutreachCommand):
        def handle(self, *args, **options):
            raise AssertionError("the guard should have stopped this")

    with patch("django.db.connection.introspection.table_names", return_value=[]):
        with pytest.raises(SystemExit) as exc:
            Reader().run_from_argv(["openoutreach", "reader"])

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.err.startswith(f"error: {ErrorType.NOT_INITIALIZED}: ")
    assert captured.out == ""


def test_the_verb_that_migrates_is_not_guarded(capsys):
    """`run` creates the schema, so it must be allowed to find none."""
    class Migrating(OpenOutreachCommand):
        requires_database = False

        def handle(self, *args, **options):
            self.stdout.write("ran")

    with patch("django.db.connection.introspection.table_names", return_value=[]):
        Migrating().run_from_argv(["openoutreach", "migrating"])

    assert capsys.readouterr().out.strip() == "ran"


# ── the provider's refusals are three different things ───────────

def _session_answering(status_code, body=None, headers=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body or {}
    resp.headers = headers or {}
    resp.raise_for_status.side_effect = None
    session = MagicMock()
    session.__enter__.return_value = session
    session.request.return_value = resp
    return session


@pytest.fixture
def keyed(db):
    from openoutreach.core.models import SiteConfig
    cfg = SiteConfig.load()
    cfg.bettercontact_api_key = "secret"
    cfg.save()
    return cfg


@pytest.mark.django_db
@pytest.mark.parametrize("status_code,expected", [
    (401, ErrorType.PROVIDER_AUTH),
    (402, ErrorType.PROVIDER_OUT_OF_CREDITS),
])
def test_each_refusal_carries_its_own_type(keyed, status_code, expected):
    with patch.object(bettercontact, "_session",
                      return_value=_session_answering(status_code)):
        with pytest.raises(BetterContactUnavailable) as exc:
            bettercontact.credit_balance()

    assert exc.value.error_type == expected


@pytest.mark.django_db
def test_an_exhausted_429_backoff_is_reported_as_rate_limited(keyed):
    """The adapter retries 429 with backoff; when it gives up, the type says why."""
    session = MagicMock()
    session.__enter__.return_value = session
    session.request.side_effect = requests.exceptions.RetryError("too many 429s")

    with patch.object(bettercontact, "_session", return_value=session):
        with pytest.raises(BetterContactUnavailable) as exc:
            bettercontact.credit_balance()

    assert exc.value.error_type == ErrorType.PROVIDER_RATE_LIMITED


def test_the_session_backs_off_on_429_and_only_on_429():
    """Retrying a 401 or a 402 would just be noise — those are final answers."""
    retry = bettercontact._session("k").get_adapter("https://x").max_retries

    assert retry.status_forcelist == (429,)
    assert retry.total == bettercontact._RATE_LIMIT_ATTEMPTS
    assert retry.backoff_factor >= 5          # seconds, doubling
    assert retry.respect_retry_after_header   # the provider's own number wins


# ── the balance the provider actually sends ──────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize("sent,expected", [
    ("520.0", 520),   # what the provider really returns — a string holding a float
    (520, 520),       # what the old check assumed, and the only shape it accepted
    (520.0, 520),
    ("0", 0),         # an empty wallet is a balance, not a failure to read one
    ("519.7", 519),   # floored: a fraction of a credit buys nothing
])
def test_the_balance_is_read_however_the_provider_spells_it(keyed, sent, expected):
    """`isinstance(credits, int)` rejected `'520.0'`, so the balance was never readable:
    `status` reported provider_unavailable against a 200 carrying the number, and the
    run's `add_credits` ask could never fire."""
    with patch.object(bettercontact, "_session",
                      return_value=_session_answering(200, {"credits_left": sent})):
        assert bettercontact.credit_balance() == expected


@pytest.mark.django_db
@pytest.mark.parametrize("body", [
    {},                          # no key at all
    {"credits_left": None},
    {"credits_left": "many"},
    {"credits_left": "-5"},      # not a balance; a provider we do not understand
])
def test_an_unreadable_balance_is_still_an_error(keyed, body):
    with patch.object(bettercontact, "_session",
                      return_value=_session_answering(200, body)):
        with pytest.raises(BetterContactUnavailable):
            bettercontact.credit_balance()
