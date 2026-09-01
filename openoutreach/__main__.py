"""The `openoutreach` console script — one install, one wizard, one command.

The verbs, in the order a reader meets them:

    openoutreach                   # onboard if needed, then find and send: the whole thing
    openoutreach run [N]           # the same, said out loud
    openoutreach init              # onboard only — both halves, one flow
    openoutreach find 10 [emails]  # find that many more, print the campaign, exit
    openoutreach send [N|all]      # mail what is already stored
    openoutreach status [--json]   # what is configured, blocked and counted

**This is an orchestrator, not a fork.** The finding is `openoutfind`'s and the sending is
`openoutsend`'s, both installed as ordinary dependencies and both hosted here as Django
apps in one registry, on one database — see `settings.py`. `find` and `status` are the
finder's own management commands, reached with their arguments and their error contract
intact; `send` is the sender's `main()`, called in this process because the sender has no
management commands. Each child still runs standalone from its own console script, and
`outfind find --json | outsend` is still the contract they implement and test against.

**A bare invocation is `run`.** The finder alone cannot default to a verb — `find` needs a
goal number, and picking one for the operator spends their credits on a guess. `run` does
not have that problem: it is onboarding, which has nothing to guess, followed by a bounded
pass whose default goal is small and stated. So `uv tool install openoutreach &&
openoutreach` is the whole first-run command, and the overview's job — answering *what can
I do* — belongs to `openoutreach -h`.

**`run` buys addresses, and that is not a flag anyone forgot.** The finder keeps spending
opt-in because `find` is free work that a forgotten flag could quietly bill for. There is
no version of *find and then email them* that does not need an address, so `run` asks for
its goal in the `emails` unit and says so before it starts: N leads carrying an address is
at most N credits, in the same unit as the invoice.

Any command accepts `--db PATH` (or `--db=PATH`) to work against a SQLite file other than
the default `~/.openoutreach/data/db.sqlite3`; the `OPENOUTREACH_DB` env var does the same.

`manage.py` is a thin shim over this module, kept for work inside a checkout.
"""

import io
import os
import sys

#: What `run` finds when nobody says otherwise. Small on purpose: the smallest number that
#: shows the whole pipeline working, and the largest bill a first run can hand somebody who
#: typed one word.
DEFAULT_GOAL = 5

OVERVIEW = """\
OpenOutreach — find B2B leads that fit, and email the ones that do.

  openoutreach                  onboard if needed, then find and send
  openoutreach run 5            ...with an explicit goal (≤5 email credits)
  openoutreach init             onboard only — both halves, one flow

  openoutreach find 10          ten more qualified leads → CSV on stdout
  openoutreach find 10 emails   ...with a verified work email (1 credit each)
  openoutreach send             mail what is already stored
  openoutreach status           what is configured, blocked and counted

  openoutreach help <command>   details for one command

Django's own commands (migrate, createsuperuser) still work.
"""

#: The verbs this project answers itself. Everything else is the finder's own command
#: registry, reached with its arguments untouched.
OURS = ("init", "send", "run")


def wants_the_overview(argv) -> bool:
    """Whether this invocation asks *what can I do*, rather than naming a command.

    A bare invocation is not one of them any more — it is `run`.
    """
    return len(argv) == 2 and argv[1] in ("-h", "--help", "help")


def extract_db_path(argv):
    """Strip `--db PATH` / `--db=PATH` out of argv, returning (rest, path_or_None).

    Django parses arguments per-command, so the flag has to come off before
    execute_from_command_line ever sees argv.
    """
    rest, db_path, i = [], None, 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--db":
            if i + 1 >= len(argv):
                sys.exit("openoutreach: --db requires a path")
            db_path = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--db="):
            db_path = arg.split("=", 1)[1]
        else:
            rest.append(arg)
        i += 1
    return rest, db_path


def main(argv=None):
    """Answer the invocation, in this process, whichever child owns the verb."""
    argv, db_path = extract_db_path(list(sys.argv if argv is None else argv))
    if wants_the_overview(argv):
        print(OVERVIEW, end="")
        return

    if db_path:
        os.environ["OPENOUTREACH_DB"] = db_path
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "openoutreach.settings")

    _hand_the_children_their_environment()

    verb = argv[1] if len(argv) > 1 else "run"
    if verb not in OURS:
        from django.core.management import execute_from_command_line

        execute_from_command_line(argv)
        return

    sys.exit(_own_verb(verb, argv[2:]))


def _hand_the_children_their_environment() -> None:
    """Export the stored answers before any verb runs, this project's own or a child's.

    **Every verb needs this, not just the ones that onboard.** `find` and `status` are the
    finder's own commands reached straight through Django, and the finder reads its
    configuration from the environment and nowhere else — so without this, an install that
    answered every question would still be told it had answered none.

    Silent when there is no schema yet: a first run reaches `run` or `init`, which
    migrates and then asks. Anything else says so itself, in its own words.
    """
    import django

    django.setup()

    from django.db import DatabaseError

    from openoutreach import wizard
    from openoutreach.config.models import SiteConfig

    try:
        config = SiteConfig.load()
    except DatabaseError:
        return
    wizard.apply_to_environment(config)


def _own_verb(verb: str, rest: list[str]) -> int:
    """Run one of this project's own verbs, rendering an expected failure as one line.

    The finder's typed errors get their contract back here: `execute_from_command_line`
    renders them for the finder's own commands, but `call_command` bypasses that, and a
    rejected API key is an answer rather than a traceback whichever verb asked for it.
    """
    import django

    django.setup()

    from cold_outreach.errors import OutsendError
    from openoutfind.core.errors import OpenOutFindError
    from openoutfind.core.management.base import format_failure

    try:
        return {"init": _init, "send": _send, "run": _run}[verb](rest)
    except OpenOutFindError as exc:
        sys.stderr.write(format_failure(exc, as_json=False))
        return 1
    except OutsendError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _init(rest: list[str]) -> int:
    """Onboard the whole install in one flow, and stop before spending anything.

    The two long fields come from files rather than flags: a product description is a page
    of markdown with newlines and apostrophes in it, and shell-quoting that is a way to
    corrupt it quietly.
    """
    import argparse

    from openoutreach import wizard

    parser = argparse.ArgumentParser(prog="openoutreach init", add_help=True)
    parser.add_argument("--product-docs", metavar="FILE",
                        help="File holding the product description (markdown).")
    parser.add_argument("--target", metavar="FILE",
                        help="File holding the target market description (markdown).")
    options = parser.parse_args(rest)

    wizard.onboard(product_docs=options.product_docs, target=options.target)
    return 0


def _send(rest: list[str]) -> int:
    """Hand `send` and its arguments to the sender, in this process.

    Its `main()` is a plain argparse entry point — the sender has no management commands
    — so this is a call, not a `call_command`. Its own `_boot()` is safe here:
    `DJANGO_SETTINGS_MODULE` is set with `setdefault` and `django.setup()` is idempotent.
    """
    from cold_outreach.__main__ import main as outsend_main

    return outsend_main(["send", *rest])


def _run(rest: list[str]) -> int:
    """Onboard if needed, find leads carrying an address, then mail them.

    The two halves meet the way they meet on the command line — JSON Lines, the contract
    from `find --json` to the sender's ingest — except that the stream is a buffer in this
    process rather than a pipe between two. That is deliberate: the format is the
    integration surface either way, and a `run` that used some privileged in-memory
    hand-off would be a second, untested path between the same two programs.
    """
    from django.core.management import call_command

    from cold_outreach.leads.ingest import ingest
    from openoutfind.core.errors import OpenOutFindError
    from openoutreach import wizard

    goal = _goal(rest)
    wizard.onboard()

    print(f"\nFinding {goal} lead(s) with a verified address — at most {goal} credits.",
          file=sys.stderr)
    found = io.StringIO()
    try:
        call_command("find", str(goal), "emails", "--json", stdout=found)
    except OpenOutFindError as exc:
        # Seven leads are seven leads: what landed before the walk stopped is already in
        # the buffer and worth sending. Only an empty one is a failed run.
        if not found.getvalue().strip():
            raise
        print(f"the search stopped short: {exc}", file=sys.stderr)

    found.seek(0)
    result = ingest(found)
    print(f"handed {result.stored} lead(s) to the sender", file=sys.stderr)

    return _send([])


def _goal(rest: list[str]) -> int:
    """`run`'s only argument: how many leads to find before sending."""
    if not rest:
        return DEFAULT_GOAL
    if len(rest) > 1 or not rest[0].isdigit() or int(rest[0]) < 1:
        sys.exit("openoutreach: run takes a number of leads, e.g. `openoutreach run 5`")
    return int(rest[0])


if __name__ == "__main__":
    main()
