# CLAUDE.md

## Rules

- **Python env**: Always use `.venv/bin/python` (not system `python3`).
- **Commits**: No `Co-Authored-By` lines. Single-line messages (no body).
- **Dependencies**: Declared in `pyproject.toml`. **Both children are required dependencies and pinned exactly** — `openoutfind==` and `openoutsend==`. `pip install openoutreach` alone has to be the whole find-then-send flow, or the second install is the friction this package exists to remove.
- **This repo holds no pipeline.** The finding is [OpenOutFind](https://github.com/eracle/OpenOutFind), the sending is [OpenOutSend](https://github.com/eracle/OpenOutSend), and both are installed packages here. **Do not add a model, a management command or an app to this project.** If a change belongs to discovery, qualification, enrichment, the CRM, the outreach agent or the mailbox, it belongs in a child repo — land it there, release it, and bump the pin here.
- **Nothing under `openoutreach/` may reimplement a child.** A duplicated fork of the finder lived here until it was deleted, and it had already started to diverge. The three modules that exist are the whole project: `settings.py` (the registry), `wizard.py` (the three gaps between the two onboardings), `__main__.py` (the verbs).
- **`openoutreach` imports `openoutsend`, deliberately.** The old rule — *nothing under `openoutreach/` may import `openoutsend`* — was written to keep the pipe honest, and the pipe is kept honest a different way now: **both children still implement and test `outfind find --json | outsend` standalone**, and `openoutreach run` uses that same JSON Lines contract through a buffer rather than a privileged in-memory hand-off. See the `openoutreach-docs` cards `p1-e3-openoutreach-single-entrypoint` and `p1-e2-find-send-boundary-contract`.
- **Each child must keep running standalone.** `uvx --from openoutfind outfind find 10` with `openoutreach` nowhere in the environment is an acceptance criterion, not a courtesy. A change here that requires a change in a child's *own* settings module is a design error.
- **There is no daemon and no web surface.** No URLconf, no Django Admin, no sessions, no templates. `run` is a bounded pass, not a loop. Do not reintroduce an unbounded process or a file the tool writes for the operator; both were tried and both were workarounds for a process that never ended.
- **Docs sync**: the CLI's contract has a second reader — `skills/find-leads/SKILL.md`, the Claude Code plugin shipped from this repo (`.claude-plugin/plugin.json` + `marketplace.json`). It restates the verbs, which of them can spend, the export columns and the `ErrorType` vocabulary, so a change to any of those has to land there too. `claude plugin validate .claude-plugin/plugin.json` checks the manifests.
- **No memory**: Never use the auto-memory system (no MEMORY.md, no memory files). Persistent context belongs in this file.
- **No API backward compat**: no external users yet — rename, delete and rewrite freely; no shims or re-export modules.
- **Migrations are the children's.** This project owns the one migration graph *over* them but writes none of its own, because it has no models. A model change in either child means bumping its pin here and re-running `migrate`.

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

Three modules, and nothing else.

- **`settings.py` — the registry.** Both children are Django *projects* that are also reusable
  *apps*; this is a third host for them. `INSTALLED_APPS` is **spelled out** rather than splatted
  from each child's `defaults.APPS` — this is the list that says what one process is, and reading
  it should not mean opening two other packages — but the *settings names* each child's apps read
  come from `defaults.app_settings()`, splatted, so a new requirement lands in one place instead of
  drifting between three settings modules. **Labels are namespaced in the children**
  (`outfind_core`, `outfind_crm`, `outsend_core`, `outsend_leads`, `outsend_emails`) because two
  apps cannot share a label; a namespaced label changes every **table name**, so never hard-code
  one — ask the model (`SiteConfig._meta.db_table`).
  - **`django.contrib.sites`** is the finder's (`setup_crm` seeds Site 1); **`auth`** is the
    sender's (`emails.Message` points at `AUTH_USER_MODEL`) and holds the one operator both
    children read.
  - **`OUTSEND_HOME` is set here, before `cold_outreach.defaults` is imported.** The sender
    resolves its own root at import time and would otherwise answer `~/.openoutsend` — a second
    home appearing behind the operator's back. That import is deliberately not at the top of the
    file.
  - **`DJANGO_ALLOW_ASYNC_UNSAFE`** is set by `openoutfind.defaults.allow_async_unsafe()` before
    `django.setup()`: the finder's agents drive async pydantic-ai from a sync boundary.
- **`wizard.py` — one onboarding.** It runs the finder's own `init` (which owns the validators, the
  live LLM check and the Legal Notice gate) and then closes **the three gaps between the two
  installs**, which is all it is for:
  1. **The five fields both singletons hold under the same names** — `product_docs`,
     `campaign_target`, `ai_model`, `llm_api_key`, `llm_api_base` — copied onto the sender's
     `SiteConfig` **through the sender's own model**. This is the reason to host both app sets in
     one registry rather than shell out: the alternative was an `env_for_outfind`/`env_for_outsend`
     translation layer restating both config surfaces as env-var strings, which drifts silently.
     The environment goes first and neither source overwrites a filled field.
  2. **The operator's name.** Both children read the same Django `User`. The finder creates it from
     the email address with no `first_name`; the sender's `_ensure_operator` then sees a user and
     skips — so every message would be signed with an email handle. Headless it reads
     `cold_outreach.first_run.OPERATOR_ENV["name"]` rather than restating the variable.
  3. **`booking_link`.** The sender offers it only alongside a missing *required* message field, and
     under this host those arrive already answered — so the one cheap moment to ask never comes.
  Then `cold_outreach.first_run.ensure_ready()` collects the mailbox; its `_ensure_llm` and
  `_ensure_message` short-circuit on stored fields, so nothing is asked or verified twice.
  **Everything is asked on stderr, the caret included** — `input`'s own prompt argument writes to
  stdout, which carries the CSV.
- **`__main__.py` — the verbs.** `--db PATH` comes off argv before Django's per-command parsing and
  sets `OPENOUTREACH_DB`. **A bare invocation is `run`** — the finder alone cannot default to a verb
  because `find` needs a goal number and picking one spends the operator's credits on a guess, but
  `run` is onboarding (nothing to guess) plus a bounded pass with a small stated goal. The overview
  therefore belongs to `-h`.
  - **Anything that is not `init`/`send`/`run` goes to `execute_from_command_line`**, so `find` and
    `status` keep their own arguments, their own progressive output and their own typed-error
    contract byte-for-byte. `migrate` and `createsuperuser` still work for the same reason.
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
alone — `tests/test_registry.py` (both app sets in one registry, two singletons, one database),
`tests/test_wizard.py` (the three gaps), `tests/test_cli.py` (what the entry point decides before
either child is asked anything).

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
