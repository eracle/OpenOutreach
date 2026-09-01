"""What the entry point decides before either child is asked anything."""
import pytest

from openoutreach import __main__ as cli


def test_the_db_flag_comes_off_before_django_sees_it():
    assert cli.extract_db_path(["openoutreach", "find", "10", "--db", "/tmp/x"]) == (
        ["openoutreach", "find", "10"], "/tmp/x")
    assert cli.extract_db_path(["openoutreach", "--db=/tmp/x", "status"]) == (
        ["openoutreach", "status"], "/tmp/x")
    assert cli.extract_db_path(["openoutreach", "status"]) == (["openoutreach", "status"], None)


def test_only_an_explicit_help_asks_for_the_overview():
    assert cli.wants_the_overview(["openoutreach", "-h"])
    assert cli.wants_the_overview(["openoutreach", "help"])
    assert not cli.wants_the_overview(["openoutreach"])
    assert not cli.wants_the_overview(["openoutreach", "help", "find"])


def test_a_bare_invocation_is_run(db, monkeypatch):
    """The whole first-run command is `openoutreach`, with no verb to learn first."""
    asked = []
    monkeypatch.setattr(cli, "_run", lambda rest: asked.append(rest) or 0)

    with pytest.raises(SystemExit) as exit:
        cli.main(["openoutreach"])

    assert exit.value.code == 0
    assert asked == [[]]


def test_send_reaches_the_sender_with_its_own_arguments(monkeypatch):
    """The sender has no management commands, so this is a call and not a call_command."""
    passed = []
    monkeypatch.setattr("cold_outreach.__main__.main", lambda argv: passed.append(argv) or 0)

    assert cli._send(["5", "--prompt-line", "opener"]) == 0
    assert passed == [["send", "5", "--prompt-line", "opener"]]


def test_runs_goal_defaults_to_something_small():
    assert cli._goal([]) == cli.DEFAULT_GOAL
    assert cli._goal(["12"]) == 12


@pytest.mark.parametrize("rest", [["nine"], ["0"], ["5", "emails"]])
def test_a_goal_that_cannot_be_read_is_an_error_not_a_default(rest):
    with pytest.raises(SystemExit):
        cli._goal(rest)
