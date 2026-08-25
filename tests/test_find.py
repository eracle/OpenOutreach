# tests/test_find.py
"""`find` — the one verb that does work, and the only one that boots the install.

Two things are pinned here. **Boot: environment first, wizard only if a human is there to
answer** — the regression an agent-driven install hits first, where the tool used to die
on a missing TTY with a message that named a mailbox (gone with the sending leg) and never
said which variables to set. And **the command's own contract**: which campaign it acts
on, what it prints, and the fact that exit 0 means the goal was met and nothing else.
"""
import contextlib
import csv
import io
import json
import logging
import webbrowser
from unittest.mock import patch

import pytest
from django.core.management import call_command

from openoutreach.core.errors import ErrorType, OpenOutreachError
from openoutreach.core.management.bootstrap import ensure_onboarded
from openoutreach.core.management.commands.find import Command, _select_campaign
from openoutreach.enrichment import bettercontact

FULL_ENV = {
    "OPENOUTREACH_PRODUCT_DESCRIPTION": "A self-hosted CI dashboard for small dev teams",
    "OPENOUTREACH_CAMPAIGN_TARGET": "book demos with CTOs at Series-A SaaS",
    "OPENOUTREACH_AI_MODEL": "anthropic:claude-sonnet-4-5-20250929",
    "OPENOUTREACH_LLM_API_KEY": "sk-test",
    "OPENOUTREACH_BETTERCONTACT_API_KEY": "bc-test",
    "OPENOUTREACH_OPERATOR_EMAIL": "me@posteo.eu",
    "OPENOUTREACH_COUNTRY": "US",
    "OPENOUTREACH_ACCEPT_LEGAL_NOTICE": "true",
}


@pytest.fixture
def headless(monkeypatch):
    """No TTY, and none of the developer's own onboarding variables."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    for name in FULL_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def command():
    cmd = Command()
    cmd.stderr = io.StringIO()
    return cmd


@pytest.fixture
def booted(campaign):
    """Skip the preamble. Migrating, bootstrapping, onboarding and validating each have
    their own tests above; none of them is what the command contract asserts.

    Patched where `find` looks them up rather than where they are defined — the command
    imports the three by name, so patching `core.management.bootstrap` would rebind a
    module attribute nothing reads."""
    with patch.object(Command, "_configure_logging"), \
            patch("openoutreach.core.management.commands.find.ensure_database"), \
            patch("openoutreach.core.management.commands.find.ensure_onboarded"), \
            patch("openoutreach.core.management.commands.find.validate_operator"):
        yield


@pytest.mark.django_db
def test_headless_and_unconfigured_names_the_variables(headless):
    with pytest.raises(OpenOutreachError) as exc:
        ensure_onboarded()

    assert exc.value.error_type == ErrorType.ONBOARDING_INCOMPLETE
    message = str(exc.value)
    assert message.startswith("error: onboarding_incomplete: ")
    assert "OPENOUTREACH_PRODUCT_DESCRIPTION" in message
    assert "OPENOUTREACH_BETTERCONTACT_API_KEY" in message
    assert "OPENOUTREACH_ACCEPT_LEGAL_NOTICE" in message
    assert "mailbox" not in message.lower()


@pytest.mark.django_db
def test_headless_and_fully_configured_runs_without_a_prompt(headless, monkeypatch):
    for name, value in FULL_ENV.items():
        monkeypatch.setenv(name, value)

    with patch("openoutreach.core.llm.verify_llm_credentials", return_value=None), \
         patch("openoutreach.core.newsletter.subscribe_to_newsletter"), \
         patch("openoutreach.core.onboarding.onboard_interactive",
               side_effect=AssertionError("wizard ran without a TTY")):
        ensure_onboarded()  # returns, having onboarded from the environment

    from openoutreach.core.onboarding import missing_keys
    assert missing_keys() == set()


@pytest.mark.django_db
def test_a_tty_still_gets_the_wizard(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    for name in FULL_ENV:
        monkeypatch.delenv(name, raising=False)

    with patch("openoutreach.core.onboarding.onboard_interactive") as wizard:
        ensure_onboarded()

    wizard.assert_called_once()


# ── choosing what to work on ─────────────────────────────────────


@pytest.mark.django_db
class TestCampaignSelection:
    """One operator with one ICP types nothing; ambiguity is never guessed at."""

    def test_the_only_campaign_needs_no_flag(self, campaign, operator):
        assert _select_campaign(None) == campaign

    def test_several_campaigns_without_a_flag_is_an_error_naming_them(self, campaign, operator):
        _campaign("Beta Co", operator)

        with pytest.raises(OpenOutreachError) as exc:
            _select_campaign(None)

        assert exc.value.error_type == ErrorType.BAD_CONFIG
        assert "Beta Co" in str(exc.value) and campaign.name in str(exc.value)

    def test_an_unknown_name_lists_the_real_ones(self, campaign, operator):
        with pytest.raises(OpenOutreachError) as exc:
            _select_campaign("Nope")

        assert campaign.name in str(exc.value)


# ── the command's contract ───────────────────────────────────────


@pytest.mark.django_db
class TestTheCommandContract:
    """What `find` prints, and what its exit code means.

    `call_command` raises whatever `handle` raises, so `OpenOutreachError` here is the
    non-zero exit the base command turns it into — see `tests/test_output_contract.py`.
    """

    def test_a_met_goal_prints_the_campaign_and_does_not_raise(self, campaign, booted):
        _exportable(campaign, "ada@acme.com")

        rows = _run("0")

        assert [row["email"] for row in rows] == ["ada@acme.com"]

    def test_zero_does_no_work_at_all(self, campaign, booted):
        with patch("openoutreach.core.cycle.run_one_action") as action:
            _run("0")

        action.assert_not_called()

    def test_an_unreached_goal_still_prints_its_rows_then_exits_non_zero(self, campaign, booted):
        """Seven leads are seven leads. The rows go to stdout either way, and the error
        line carries the type an agent branches on."""
        _exportable(campaign, "ada@acme.com")
        out = io.StringIO()

        with patch("openoutreach.core.cycle.run_one_action", return_value=False):
            with pytest.raises(OpenOutreachError) as exc:
                call_command("find", "5", stdout=out)

        assert exc.value.error_type == ErrorType.GOAL_UNREACHED
        assert "0 of 5 leads" in str(exc.value)
        assert len(list(csv.DictReader(io.StringIO(out.getvalue())))) == 1

    def test_the_whole_campaign_prints_not_just_this_run(self, campaign, booted):
        """What makes `> leads.csv` correct by construction: the newest file supersedes
        every earlier one."""
        _exportable(campaign, "old@acme.com")

        with patch("openoutreach.core.cycle.run_one_action",
                   side_effect=lambda c, buy_addresses=True, max_new_lookups=None: bool(
                       _exportable(c, "new@acme.com"))):
            rows = _run("1")

        assert {row["email"] for row in rows} == {"old@acme.com", "new@acme.com"}

    def test_new_narrows_to_what_this_run_produced(self, campaign, booted):
        _exportable(campaign, "old@acme.com")

        with patch("openoutreach.core.cycle.run_one_action",
                   side_effect=lambda c, buy_addresses=True, max_new_lookups=None: bool(
                       _exportable(c, "new@acme.com"))):
            rows = _run("1", "--new")

        assert [row["email"] for row in rows] == ["new@acme.com"]

    def test_json_puts_the_records_on_stdout_one_per_line(self, campaign, booted):
        """JSON Lines, so a stream truncated mid-run has still delivered every complete
        record before the break — and the full record, profile text included, which is
        the field the CSV projection drops."""
        _exportable(campaign, "ada@acme.com")
        out = io.StringIO()

        call_command("find", "0", "--json", stdout=out)

        lines = out.getvalue().splitlines()
        assert [json.loads(line)["email"] for line in lines] == ["ada@acme.com"]
        assert "profile_text" in json.loads(lines[0])

    def test_json_puts_the_run_metadata_on_stderr_and_nothing_else(self, campaign, booted, capsys):
        """Otherwise a `2>` capture is prose with an object somewhere in it, and every
        caller writes the same fragile `tail -1`."""
        _exportable(campaign, "ada@acme.com")

        call_command("find", "0", "--json", stdout=io.StringIO())

        document = json.loads(capsys.readouterr().err)  # a banner or a log line would raise
        assert document["reached"] is True and document["stopped_because"] is None
        assert document["goal"] == {"count": 0, "unit": "leads"}
        assert document["rows"] == 1

    def test_a_negative_count_is_refused(self, campaign, booted):
        with pytest.raises(OpenOutreachError) as exc:
            call_command("find", "-1", stdout=io.StringIO())

        assert exc.value.error_type == ErrorType.BAD_CONFIG

    def test_buying_is_off_by_default(self, campaign, booted):
        """A bare `find` cannot spend, however many deals are queued past the gate.

        This is the inversion of 2026-08-21: buying used to be on unless `--no-emails`
        turned it off, so a run counting *leads* quietly bought addresses. A flag you
        forget should cost a feature, never money.
        """
        _exportable(campaign, "ada@acme.com")

        with patch("openoutreach.core.cycle.run_one_action",
                   return_value=False) as action:
            with pytest.raises(OpenOutreachError):
                call_command("find", "1", stdout=io.StringIO())

        assert action.call_args.kwargs["buy_addresses"] is False

    def test_emails_flag_reaches_the_cycle(self, campaign, booted):
        """The flag is only worth having if it arrives where the spending happens."""
        with patch("openoutreach.core.cycle.run_one_action",
                   return_value=False) as action:
            with pytest.raises(OpenOutreachError):
                call_command("find", "1", "--emails", stdout=io.StringIO())

        assert action.call_args.kwargs["buy_addresses"] is True

    def test_an_emails_goal_implies_the_flag(self, campaign, booted):
        """The noun says what to count and the flag says what may be paid for — but a
        goal counted in addresses cannot be met without buying them."""
        with patch("openoutreach.core.cycle.run_one_action",
                   return_value=False) as action:
            with pytest.raises(OpenOutreachError):
                call_command("find", "5", "emails", stdout=io.StringIO())

        assert action.call_args.kwargs["buy_addresses"] is True

    def test_open_without_a_browser_fails_before_any_work(self, campaign, booted):
        """A flag that silently does nothing is the bug you find at 2am."""
        with patch("webbrowser.get", side_effect=webbrowser.Error), \
                patch("openoutreach.core.cycle.run_one_action") as action:
            with pytest.raises(OpenOutreachError) as exc:
                call_command("find", "1", "--open", stdout=io.StringIO())

        assert exc.value.error_type == ErrorType.BAD_CONFIG
        action.assert_not_called()

    def test_minute_zero_states_the_goal_and_whether_it_can_spend(self, campaign, booted, caplog):
        """Spending is opt-in at every layer, which is a good default and an invisible
        one. An operator who expected addresses should learn it in the first line, not
        from an empty column at the end."""
        with caplog.at_level(logging.INFO):
            call_command("find", "0", stdout=io.StringIO())

        assert "finding only, no addresses bought" in caplog.text

    def test_asking_to_buy_says_so_before_any_work(self, campaign, booted, caplog):
        with patch("openoutreach.core.cycle.run_one_action", return_value=False), \
                caplog.at_level(logging.INFO):
            with pytest.raises(OpenOutreachError):
                call_command("find", "1", "--emails", stdout=io.StringIO())

        assert "buying addresses, one credit each" in caplog.text

    def test_the_icp_echo_names_who_it_is_looking_for(self, campaign, booted, caplog):
        """The earliest possible proof the product description was understood — and the
        earliest chance to correct it, which is the loop the README sells."""
        campaign.anchor_profiles = ["vp of engineering saas acme senior california united states"]
        campaign.save(update_fields=["anchor_profiles"])

        with caplog.at_level(logging.INFO):
            call_command("find", "0", stdout=io.StringIO())

        assert "Looking for people like:" in caplog.text
        assert "vp of engineering saas acme" in caplog.text

    def test_an_unanchored_campaign_echoes_nothing(self, campaign, booted, caplog):
        """A first run has no anchors yet — they are written during the job, and print
        themselves there. Silence beats a heading with nothing under it."""
        with caplog.at_level(logging.INFO):
            call_command("find", "0", stdout=io.StringIO())

        assert "Looking for people like:" not in caplog.text

    def test_the_run_ends_with_the_ask_and_the_csv_stays_a_csv(self, campaign, booted, caplog):
        """A run that leaves ranked leads behind and an empty wallet has to say so.

        The sentence is `status`'s, rendered here — the run derives nothing. It goes to
        stderr with everything else that is not a row: a stray line in a CSV is not a
        CSV, and this one carries a URL.
        """
        _exportable(campaign, "ada@acme.com")
        _ranked(campaign)
        out = io.StringIO()

        with _wallet(balance=0), caplog.at_level(logging.INFO):
            call_command("find", "0", stdout=out)

        assert "0 credits left" in caplog.text
        assert bettercontact.SIGNUP_URL in caplog.text
        # Both rows export — the ranked one with a blank address, which is the whole
        # reason it is still waiting.
        rows = list(csv.DictReader(io.StringIO(out.getvalue())))
        assert sorted(row["email"] for row in rows) == ["", "ada@acme.com"]

    def test_json_carries_the_next_action_for_the_agent_to_relay(self, campaign, booted,
                                                                 caplog, capsys):
        """An agent reads the object, not the log, so the ask has to be in it."""
        _ranked(campaign)

        with _wallet(balance=0), caplog.at_level(logging.INFO):
            call_command("find", "0", "--json", stdout=io.StringIO())

        document = json.loads(capsys.readouterr().err)
        assert document["next_action"]["type"] == "add_credits"
        assert document["next_action"]["leads"] == 1
        assert "Next:" not in caplog.text  # the object is the whole answer

    def test_debug_is_the_shorthand_for_log_level_debug(self, campaign, booted):
        """Both flags write the same dest, so they cannot disagree."""
        from openoutreach.core.management.commands.find import Command

        with patch("openoutreach.core.cycle.run_one_action", return_value=False), \
                patch.object(Command, "_configure_logging") as configure:
            call_command("find", "0", "--debug", stdout=io.StringIO())

        assert configure.call_args.args[0] == "debug"


# ── helpers ──────────────────────────────────────────────────────


def _campaign(name, operator):
    from openoutreach.core.models import Campaign

    row = Campaign.objects.create(name=name)
    row.users.add(operator)
    return row


def _exportable(campaign, email):
    """One lead the export would write: judged, accepted, and carrying an address."""
    from openoutreach.crm.models import DealState
    from tests.factories import DealFactory, LeadFactory

    return DealFactory(campaign=campaign, lead=LeadFactory(email=email),
                       state=DealState.RESOLVED, reason="fits the ICP")


def _ranked(campaign):
    """One lead that cannot advance without a credit."""
    from openoutreach.crm.models import DealState
    from tests.factories import DealFactory, LeadFactory

    return DealFactory(campaign=campaign, lead=LeadFactory(email=None),
                       state=DealState.READY_TO_FIND_EMAIL, reason="fits the ICP")


@contextlib.contextmanager
def _wallet(balance):
    """A configured provider with a known balance, and onboarding out of the way — the
    two inputs the next action is derived from."""
    with patch("openoutreach.core.onboarding.missing_env_keys", return_value={}), \
            patch("openoutreach.enrichment.bettercontact.is_configured", return_value=True), \
            patch("openoutreach.enrichment.bettercontact.credit_balance", return_value=balance):
        yield


def _run(*args):
    """Run `find` and parse the CSV it printed."""
    out = io.StringIO()
    call_command("find", *args, stdout=out)
    return list(csv.DictReader(io.StringIO(out.getvalue())))
