# CLAUDE.md

## Rules

- **Python env**: Always use `.venv/bin/python` (not system `python3`).
- **Commits**: No `Co-Authored-By` lines. Single-line messages (no body).
- **Dependencies**: Managed in `requirements/*.txt` (used by local dev and Docker).
- **Docs sync**: When modifying code, update CLAUDE.md and ARCHITECTURE.md to reflect changes.
- **No memory**: Never use the auto-memory system (no MEMORY.md, no memory files). All persistent context belongs in CLAUDE.md or ARCHITECTURE.md.
- **Error handling**: App should crash on unexpected errors. `try/except` only for expected, recoverable errors. Custom exceptions in `exceptions.py`.
- **No API backward compat**: Project has no external users yet — don't preserve old Python APIs, function signatures, or import paths. Rename, delete, and rewrite freely; no shims or re-export modules. DB schema changes still go through Django migrations as normal — existing installs must upgrade cleanly.
- **Migrations at the end**: During a multi-file change, let the models settle first — generate migrations in one pass at the end of the sweep, then run the suite. Don't hand-write a migration mid-change.

## Project Overview

OpenOutreach — a self-hosted, **email-first** AI sales agent that learns your ICP and runs the whole funnel with **zero platform-ToS surface** (browserless; no LinkedIn account, no scraping):

**define ICP → discover → qualify → rank → find email → agentic email from your own mailbox.**

- **Discovery** is a licensed source — BetterContact **Lead Finder** (ICP search returns firmographic profiles, no emails, billed nothing).
- **Qualification** is the crown jewel — per-campaign **GPR + BALD active learning** over 384-dim embeddings, decided by an LLM.
- **Enrichment** is the one paid step — BetterContact resolves a work email for the top-ranked leads only (one credit per verified hit), fronted by a free cross-operator cache (the hub).
- **Outreach** is agentic email from mailboxes the user owns (SMTP send + IMAP reply-reading), driven by the same LLM follow-up agent.

Django + Django Admin own the CRM/ORM (models are this project's); pydantic-ai drives the agents.

## Commands

```bash
# Docker
make build / make up / make stop / make logs / make up-view

# Local dev
make setup    # install deps + migrate + bootstrap CRM
make run      # run the daemon (== python manage.py rundaemon; bare manage.py also defaults to it)
make admin    # Django Admin at localhost:8000/admin/

# Testing
make test / make docker-test
.venv/bin/pytest tests/test_qualify.py   # single file
.venv/bin/pytest -k test_name            # single test

# Operator JSON control plane (Hermes/agents)
.venv/bin/python manage.py oo status --json
.venv/bin/python manage.py oo campaign list --json
.venv/bin/python manage.py oo lead list --campaign "Campaign" --state "Ready to Email" --json
.venv/bin/python manage.py oo task list --json
.venv/bin/python manage.py oo email send-next --campaign "Campaign" --dry-run --idempotency-key key-1 --json
.venv/bin/python manage.py oo email send-next --campaign "Campaign" --non-interactive --idempotency-key key-2 --json
.venv/bin/python manage.py oo audit list --json --limit 50
```

## Architecture (quick reference)

For detailed module docs, see `ARCHITECTURE.md`.

- **Entry**: `manage.py` — stock Django management; bare `python manage.py` (no subcommand) defaults to `rundaemon`. The `rundaemon` command (`core/management/commands/rundaemon.py`) runs: migrate (+ `setup_crm`) → onboard if incomplete → validate → `run_daemon`. **Onboarding** (`core/onboarding.py`) is interactive and email-first — product/objective → LLM (live-verified) → **mailbox** (paste app passwords → SMTP auth-check) → **BetterContact key** → **your email** → country → newsletter/legal. Both the mailbox and the BetterContact key are **mandatory** (BetterContact powers *both* discovery and enrichment). The `account` step asks the operator's **own email** (required) — the human's inbox, stored as `User.email` and used as the BCC-copy + newsletter target + contacts key; it is deliberately **distinct** from the mailbox `from_address` (the sending identity). The operator `User` is created only after a mailbox exists. Built as an ordered list of idempotent `Step`s (`is_done`/`run`, persist-as-you-go) that resume where they left off; interactive prompt primitives vendored in `onboarding_wizard.py` (no external `openoutreach` dep).
- **State machine**: `crm/models/deal.py:DealState` (OpenOutreach-owned `TextChoices`) — `QUALIFIED → READY_TO_FIND_EMAIL → FINDING_EMAIL → READY_TO_EMAIL → SENDING_EMAIL → EMAILED → COMPLETED / FAILED`. The GP confidence gate promotes `QUALIFIED → READY_TO_FIND_EMAIL` (rationing the paid lookup). The paid lookup is a **two-leg async handshake** (mirroring the retired connect→check_pending): `find_email` (submit) resolves free-hub-first (hit → `READY_TO_EMAIL` directly) else fires a provider job and parks the deal at `FINDING_EMAIL`; `collect_email` (poll) then checks the job's `request_id` — hit → `READY_TO_EMAIL`, miss → `FAILED reason="no email"` with **blank outcome** so the ML labeler skips it, couldn't-run/timeout → back to `READY_TO_FIND_EMAIL`. The job handle + poll backoff live in the **collect task's payload**, never on the deal. The opener first claims `READY_TO_EMAIL → SENDING_EMAIL` before compose/SMTP, then records the send and parks at `EMAILED`, where the agentic follow-up loop reads replies and decides send/wait/complete until terminal. `Outcome` (converted/not_interested/wrong_fit/…) on `Deal.outcome`; `Lead.disqualified=True` = permanent account-level exclusion; LLM rejections = `FAILED` + `wrong_fit` (campaign-scoped). *(The LinkedIn connect leg — browser, Voyager, connect/check_pending, `linkedin_cli` — was removed; the `legacy` app is a model-less migration-history anchor.)*
- **Task queue + control plane**: `Task` model (persistent). Four types — `find_email`, `collect_email`, `follow_up`, `email` — handled in `openoutreach/emails/tasks/` (`handle_find_email` / `handle_collect_email` / `handle_follow_up` / `handle_email`), signature `handle_*(task, session, qualifiers)`. Two shapes of row: **lazy drains** (`find_email`/`email`/`follow_up`) carry `payload = {"campaign_id": <id>}` only and resolve their target at run time; the **bound poll** (`collect_email`) carries the in-flight lookup's `request_id`, `provider`, `submitted_at`, and backoff `attempt` in its payload. Slot creation is centralized in `core/scheduler.py` — nothing else inserts `Task` rows. There is **no spend cap or Poisson pacing**: paid spend rides on send capacity. `flush_find_email_queue` mints a submit slot only when there's mailbox send-headroom for the result *today* (`remaining_today()` minus deals already in the send pipeline, `READY_TO_EMAIL + SENDING_EMAIL + FINDING_EMAIL`); the GP gate rations *which* leads qualify, the send cap bounds *how many* lookups ride the pipeline. `flush_email_queue` / `flush_follow_up_queue` eager-drain ready openers / due follow-ups, capped by the same per-box headroom. `collect_email` polls are **self-chaining** (each still-running poll mints its successor with doubled backoff), so one live poll exists per lookup. `Task.pending()` claims in opportunity-cost order — `follow_up` > `collect_email` > `email` > `find_email` — while `seconds_to_next()` sleeps by earliest `scheduled_at` alone (so a soon low-priority task never oversleeps behind a far-future high-priority one). `reconcile(session)` recovers crash-stale RUNNING tasks and tops up the drains — on startup and every idle cycle. `core/management/commands/oo.py` is the JSON operator control plane for agents: read status/campaign/lead/task state, dry-run or execute one email send with idempotency, and inspect `ActionLog` audit rows. Every JSON command emits the same `ok/command/status/dry_run/action_id/result/error/warnings` envelope.
- **ML pipeline**: GPR (sklearn) + BALD active learning + LLM qualification, over 384-dim FastEmbed vectors cached on `Lead.embedding`. Per-campaign GP models in `Campaign.model_blob`.
- **Discovery + enrichment**: `openoutreach/discovery.py` (Lead Finder `search`/`embed_row`, free) uses the blocking `submit_and_poll` transport; `emails/bettercontact.py` (paid finder) splits it into `submit(query) → request_id` + `poll_once(request_id) → PollOutcome` so the daemon never blocks on a poll (the `collect_email` leg owns the backoff). `core/pipeline/`: `icp.py` (LLM → Lead Finder filters, cached on `Campaign.icp_filters`), `discover.py` (page the ICP into embedded `Lead`s, cursor `Campaign.discovery_offset`), `qualify.py` → `ready_pool.py` (GP gate) → `pools.py` (the composable generator chain; discovers a fresh page when the pool goes dry).
- **Contacts store (hub)**: `contacts/service.py` — an optional free `profile_url → email` cache at `hub.openoutreach.app`, tried *before* the paid finder and given back to on a fresh paid hit. Both calls are best-effort (outage/no-token → no-op) and gated on `has_mailbox()`. The give-back is **non-EEA only** and derived from the operator's onboarding country (`not is_eea_located`) — never a stored toggle; the server re-gates authoritatively. A per-operator token is earned on first contribution and stored in `SiteConfig.contacts_api_token` (never the repo).
- **Config**: `SiteConfig` DB singleton — `ai_model` (a pydantic-ai `provider:model` id, e.g. `anthropic:claude-sonnet-4-5-20250929`; bare `gpt-*`/`o1`/`o3`/`claude-*`/`gemini-*` auto-prefixed), `llm_api_key`, `llm_api_base` (only for `openai_compatible:*`), `bettercontact_api_key` (blank disables discovery + enrichment), `contacts_api_token`/`contacts_api_url`, `country_code` (the only persisted operator setting — drives timezone + email jurisdiction). `core/conf.py`: `CAMPAIGN_CONFIG` (ML + human-rhythm defaults, incl. `min_gp_confidence`), `COLLECT_BACKOFF_BASE_S`/`COLLECT_BACKOFF_MAX_S`/`COLLECT_DEADLINE_S` (the `collect_email` poll backoff — paid spend is gated by send-headroom, not a cap), `DEFAULT_EMAIL_DAILY_LIMIT`, active-hours knobs (`ENABLE_ACTIVE_HOURS`/`ACTIVE_*`; `ACTIVE_TIMEZONE=None` → resolved from the operator's country).
- **Django apps** (all nested under `openoutreach/`, dotted `AppConfig.name`, short labels): `core` (engine — daemon, task queue + scheduler, Campaign/SiteConfig/Task/ActionLog, llm, onboarding, ML, discovery/qualify pipeline, the two agents, JSON control plane), `crm` (Lead + Deal), `chat` (ChatMessage — the per-Deal conversation), `emails` (discovery/enrichment client, Mailbox + import + SMTP/IMAP, sender, the three task handlers), `legacy` (model-less migration-history anchor). `contacts` is a service-only module (no models, not an installed app). One engine, one channel.
- **Data dir**: `data/` holds `db.sqlite3`. Docker mounts a volume at `/app/data`.
- **Docker**: `python:3.12-slim` multi-stage build with `uv`; no browser, no VNC. `compose/openoutreach/Dockerfile`. `BUILD_ENV` arg selects requirements.
- **CI/CD**: `.github/workflows/tests.yml` (pytest), `deploy.yml` (on `v*` tags → build + push to `ghcr.io`).
