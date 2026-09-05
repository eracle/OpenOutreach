# CLAUDE.md

## Rules

- **Python env**: Always use `.venv/bin/python` (not system `python3`).
- **Commits**: No `Co-Authored-By` lines. Single-line messages (no body).
- **Dependencies**: Declared in `pyproject.toml`. **Both children are required dependencies and pinned exactly** — `openoutfind==` and `openoutsend==`. `pip install openoutreach` alone has to be the whole find-then-send flow, or the second install is the friction this package exists to remove. **Always bump the pin here after pushing a change to either child's `main`.** Both children auto-publish a new PyPI version on every green push, so the moment a fix lands there it exists as an installable release — check the new version (PyPI, or `git rev-list --count v<base>..HEAD` in the child, which is exactly the patch number CI derives), edit the pin, reinstall (`uv pip install -e ".[dev]"`), and rerun this repo's suite before calling the cross-repo change done. A stale pin here is a real install running old, unfixed child code even though the fix already shipped — do not treat the pin bump as a follow-up task.
- **This repo holds no pipeline.** The finding is [OpenOutFind](https://github.com/eracle/OpenOutFind), the sending is [OpenOutSend](https://github.com/eracle/OpenOutSend), and both are installed packages here. **Do not add a pipeline model or a management command to this project.** If a change belongs to discovery, qualification, enrichment, the CRM, the outreach agent or the mailbox, it belongs in a child repo — land it there, release it, and bump the pin here. **The one exception is the one app that exists**: `openoutreach.config.SiteConfig`, the answers a person gave. The children read their configuration from the environment and remember none of it, so somebody has to remember what was typed, and it is not them.
- **Nothing under `openoutreach/` may reimplement a child.** A duplicated fork of the finder lived here until it was deleted, and it had already started to diverge. What exists is the whole project: `settings.py` (the registry), `config/` (the one model), `wizard.py` (the questions, and the export into both children's variables), `__main__.py` (the verbs).
- **`openoutreach` imports `openoutsend`, deliberately.** The old rule — *nothing under `openoutreach/` may import `openoutsend`* — was written to keep the pipe honest, and the pipe is kept honest a different way now: **both children still implement and test `outfind find --json | outsend` standalone**, and `openoutreach run` uses that same JSON Lines contract through a buffer rather than a privileged in-memory hand-off. See the `openoutreach-docs` cards `p1-e3-openoutreach-single-entrypoint` and `p1-e2-find-send-boundary-contract`.
- **Each child must keep running standalone.** `uvx --from openoutfind outfind find 10` with `openoutreach` nowhere in the environment is an acceptance criterion, not a courtesy. A change here that requires a change in a child's *own* settings module is a design error.
- **There is no daemon and no web surface.** No URLconf, no Django Admin, no sessions, no templates. `run` is a bounded pass, not a loop. Do not reintroduce an unbounded process or a file the tool writes for the operator; both were tried and both were workarounds for a process that never ended.
- **Docs sync**: the CLI's contract has a second reader — `skills/find-leads/SKILL.md`, the Claude Code plugin shipped from this repo (`.claude-plugin/plugin.json` + `marketplace.json`). It restates the verbs, which of them can spend, the export columns and the `ErrorType` vocabulary, so a change to any of those has to land there too. `claude plugin validate .claude-plugin/plugin.json` checks the manifests.
- **No memory**: Never use the auto-memory system (no MEMORY.md, no memory files). Persistent context belongs in this file.
- **No API backward compat**: no external users yet — rename, delete and rewrite freely; no shims or re-export modules.
- **Migrations are almost entirely the children's.** This project owns the one migration graph *over* them and writes only its own config app's (`openoutreach/config/migrations/`). A model change in either child means bumping its pin here and re-running `migrate`.

## Project Overview

**OpenOutreach is an orchestrator.** It installs `openoutfind` and `openoutsend`, hosts both
children's Django apps in **one app registry, one SQLite file, one migration graph, one process**,
and puts one wizard and one command in front of them.

The problem it solves is activation cost, not capability: getting from *"I want leads and I want
them sent"* to a working pipeline used to take two installs, two onboarding wizards and two env
files. It takes `uv tool install openoutreach && openoutreach` now.

```
openoutreach                  # onboard if needed, then find and send
openoutreach run 5            # ...with an explicit goal
openoutreach init             # onboard only — both halves, one flow
openoutreach find 10 [emails] # the finder's own verb, arguments and error contract intact
openoutreach send [N|all]     # the sender's own verb
openoutreach status [--json]  # the finder's own verb
```

## Architecture

Three modules and one app, and nothing else.

- **`settings.py` — the registry.** Both children are Django *projects* that are also reusable
  *apps*; this is a third host for them. `INSTALLED_APPS` is **spelled out** rather than splatted
  from each child's `defaults.APPS` — this is the list that says what one process is, and reading
  it should not mean opening two other packages — but the *settings names* each child's apps read
  come from `defaults.app_settings()`, splatted, so a new requirement lands in one place instead of
  drifting between three settings modules. **Labels are namespaced in the children**
  (`outfind_core`, `outfind_crm`, `outsend_core`, `outsend_leads`, `outsend_emails`) because two
  apps cannot share a label; a namespaced label changes every **table name**, so never hard-code
  one — ask the model (`Lead._meta.db_table`). This host's own app is namespaced for the same
  reason: `openoutreach_config`, never a bare `config`.
  - **`django.contrib.sites`** is the finder's (`setup_crm` seeds Site 1); **`auth`** is the
    sender's (`emails.Message` points at `AUTH_USER_MODEL`) and holds the one operator both
    children read.
  - **`OUTSEND_HOME` is set here, before `cold_outreach.defaults` is imported.** The sender
    resolves its own root at import time and would otherwise answer `~/.openoutsend` — a second
    home appearing behind the operator's back. That import is deliberately not at the top of the
    file.
  - **`DJANGO_ALLOW_ASYNC_UNSAFE`** is set by `openoutfind.defaults.allow_async_unsafe()` before
    `django.setup()`: the finder's agents drive async pydantic-ai from a sync boundary.
- **`config/models.py` — the one model, and the two vocabularies.** `SiteConfig` is a singleton
  holding every answer a person gave, and `export()` renders it as `OPENOUTFIND_*` + `OUTSEND_*`.
  Two maps, `FINDER_ENV` and `SENDER_ENV`, keyed by field — **never merged**, because most fields
  appear in both and merging silently keeps one child's variable and drops the other's. The
  suffixes are not always the same (`OUTSEND_OPERATOR_NAME` against a finder that signs nothing;
  `OPENOUTFIND_OPERATOR_COUNTRY` and `OUTSEND_OPERATOR_COUNTRY` happen to share a name), which is why the
  mapping is written down rather than derived from a prefix. **A blank field exports nothing**: to a
  child, unset means *use your default* (the sender's Google SMTP host) while a blank value
  overrides that default with nothing.
- **`wizard.py` — one onboarding.** Migrate → ask for whatever is still missing → save the row →
  `apply_to_environment` → let each child check what it was handed (`openoutfind.core.readiness`
  and `cold_outreach.first_run`, unchanged and unwrapped, so there is one place that knows what a
  find needs and one that knows what a send needs). Three rules:
  - **Ask only what is missing, and never twice.** A question is skipped when the row answers it
    *or the environment already carries it* — an operator who exported their own variables has
    already given that answer.
  - **`apply_to_environment` uses `setdefault`.** A variable set for this run was set on purpose;
    a stored answer quietly reverting it is exactly the failure the children's own "the environment
    seeds, it never reverts" rule existed to prevent.
  - **Headless, there is nobody to ask**, so a missing answer is reported as the variable that
    would supply it — the same vocabulary the children use, so whatever a person is asked here an
    agent sets there.
  **Exporting is not the translation layer the old design refused.** That objection held while each
  child *also* had a config model — two surfaces for the strings to drift against. With the
  environment as a child's only surface, this writes the one interface the child has, and a child
  whose variable stops arriving says so by name on its next run.
  **Everything is asked on stderr, the caret included** — `input`'s own prompt argument writes to
  stdout, which carries the CSV.
- **`__main__.py` — the verbs.** `--db PATH` comes off argv before Django's per-command parsing and
  sets `OPENOUTREACH_DB`. **A bare invocation is `run`** — the finder alone cannot default to a verb
  because `find` needs a goal number and picking one spends the operator's credits on a guess, but
  `run` is onboarding (nothing to guess) plus a bounded pass with a small stated goal. The overview
  therefore belongs to `-h`.
  - **Every verb is preceded by the export**, not only the ones that onboard.
    `_hand_the_children_their_environment()` calls `django.setup()`, loads the row and applies it
    before the verb is dispatched — `find` and `status` are the finder's own commands and the finder
    reads its configuration from the environment and nowhere else, so without this an install that
    answered every question would be told it had answered none. It is silent on a `DatabaseError`:
    a first run reaches `run` or `init`, which migrates and then asks.
  - **Anything that is not `init`/`send`/`run` goes to `execute_from_command_line`**, so `find` and
    `status` keep their own arguments, their own progressive output and their own typed-error
    contract byte-for-byte. `migrate` and `createsuperuser` still work for the same reason.
  - **`init` takes `--product-docs FILE` and `--target FILE`**, and nothing else. The two long
    fields are pages of markdown; shell-quoting one is a way to corrupt it quietly. No finder flags
    pass through any more — the finder has no `init`, it has `check`.
  - **`send` is a call, not a `call_command`** — the sender has no management commands; its CLI is
    `argparse` in `cold_outreach/__main__.py:main`. Its `_boot()` is safe here:
    `DJANGO_SETTINGS_MODULE` is set with `setdefault` and `django.setup()` is idempotent.
  - **`run` is `find --json` into a buffer, then the sender's own ingest, then `send`.** The two
    halves meet the way they meet on the command line; a privileged in-memory hand-off would be a
    second, untested path between the same two programs. A `find` that stops short does **not**
    abort the run — what landed in the buffer is worth sending, and only an empty buffer is a
    failed run.
  - **`run` buys addresses, and that is not a flag anyone forgot.** The finder keeps spending
    opt-in because `find` is free work a forgotten flag could quietly bill for. There is no version
    of *find and then email them* that does not need an address, so `run` asks for its goal in the
    `emails` unit — N leads is at most N credits — and says so before it starts.
  - **Expected failures are one line.** `call_command` bypasses the finder's `run_from_argv`, so
    `_own_verb` catches `OpenOutFindError` and renders it with the finder's own `format_failure`,
    and `OutsendError` the sender's way. A rejected key is an answer, not a traceback.

## Commands

```bash
# Installed (what an operator runs)
uv tool install openoutreach && openoutreach
openoutreach run 5
openoutreach find 10 emails > leads.csv
openoutreach send 5
openoutreach status --json

# Local dev
make setup                      # install -e ".[dev]" + migrate
make run N=5 / make find N=10 UNIT=emails / make send N=5 / make status
make test
.venv/bin/pytest tests/test_wizard.py

# Docker — the server deploy only
make build / make up / make stop / make logs
```

## Testing

The suite is small on purpose: the children test their own pipelines, and duplicating that here
would be the fork this project just deleted. What is tested is only what neither child can check
alone — `tests/test_registry.py` (both app sets in one registry, exactly one config model, one
database), `tests/test_wizard.py` (the row, the export into two vocabularies, and what is asked
for), `tests/test_cli.py` (what the entry point decides before either child is asked anything).

## CI/CD

`.github/workflows/tests.yml` (native pytest on 3.12), `deploy.yml` (**every push to `main`**, plus
`v*` tags → tests → build + push `ghcr.io/eracle/openoutreach` → `repository-dispatch:
image-updated` to the hub repo). **Every green push to `main` also publishes to PyPI** via trusted
publishing (`publish-pypi`, environment `pypi` — the trusted publisher is registered against that
workflow *filename* and that environment name, so neither may be renamed, and the environment must
carry **no required reviewer** or every push would wait on a click). **The version is derived, never
committed**: `pyproject.toml`'s `version` is the base, and the patch is
`git rev-list --count v0.1.0..HEAD` at publish time — hence `fetch-depth: 0`. `skip-existing` makes
a re-run a no-op. There is no release gate beyond `needs: test`.
