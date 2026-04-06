# CLAUDE.md

## Rules

- **Python env**: Always use `.venv/bin/python` (not system `python3`).
- **Commits**: No `Co-Authored-By` lines. Single-line messages (no body).
- **Dependencies**: Managed in `requirements/*.txt` (used by local dev and Docker).
- **Docs sync**: When modifying code, update CLAUDE.md and ARCHITECTURE.md to reflect changes.
- **No memory**: Never use the auto-memory system (no MEMORY.md, no memory files). All persistent context belongs in CLAUDE.md or ARCHITECTURE.md.
- **Error handling**: App should crash on unexpected errors. `try/except` only for expected, recoverable errors. Custom exceptions in `exceptions.py`.
- **No backward compat**: CRM models are owned by this project — no need for backward compatibility shims, legacy migration code, or re-export modules. Simplify freely.

## Project Overview

OpenOutreach — self-hosted LinkedIn automation for B2B lead generation. Playwright + stealth for browser automation, LinkedIn Voyager API for profile data, Django for CRM (models owned by this project). Custom web UI (Tailwind CSS + HTMX + Chart.js) replaces Django Admin as the primary interface.

## Commands

```bash
# Docker
make build / make up / make stop / make attach / make up-view

# Local dev
make setup    # install deps + browsers + migrate + bootstrap CRM
make run      # run daemon
make admin    # CRM web UI at localhost:8000/crm/

# Testing
make test / make docker-test
pytest tests/api/test_voyager.py   # single file
pytest -k test_name                # single test
```

## Architecture (quick reference)

For detailed module docs, see `ARCHITECTURE.md`.

- **Entry**: `manage.py` — stock Django management. `rundaemon` command (premigrations → migrate → onboard → validate → task queue loop). `manage.py` with no args defaults to `rundaemon`. Onboarding logic in `onboarding.py`: `OnboardConfig` (pure dataclass), `missing_keys()`, `collect_from_wizard()`, single `apply()` write path. Docker `start` script handles Xvfb/VNC, then `exec python manage.py rundaemon`.
- **Web startup**: CRM UI at `/crm/accounts/` can start/stop daemons per profile via `daemon_manager.py`. Each profile gets its own thread + browser. `run_daemon()` accepts optional `stop_event` for graceful shutdown.
- **First-time setup**: `FirstTimeSetupMiddleware` redirects to `/setup/` if no superuser exists. One-time admin creation page, auto-login, redirect to CRM.
- **Root URL**: `/` redirects to `/crm/`. `LOGIN_URL` points to `/accounts/login/` (CRM login page).
- **Premigrations**: `linkedin/premigrations/` — numbered Python files for pre-Django filesystem changes (run before `migrate`). Tracked via `data/.premigrations` JSON file. Add new migrations as `NNNN_description.py` with a `forward(root_dir)` function.
- **State machine**: `enums.py:ProfileState` — QUALIFIED → READY_TO_CONNECT → PENDING → CONNECTED → COMPLETED / FAILED. Deal.state is a CharField with ProfileState choices (no Stage model). `ClosingReason` (COMPLETED/FAILED/DISQUALIFIED) on Deal.closing_reason. `Lead.disqualified=True` = permanent exclusion. LLM rejections = FAILED Deals with DISQUALIFIED closing reason (campaign-scoped).
- **Task queue**: `Task` model (persistent). Three types: `connect`, `check_pending`, `follow_up`. Handlers in `linkedin/tasks/`, signature: `handle_*(task, session, qualifiers)`.
- **ML pipeline**: GPR (sklearn) + BALD active learning + LLM qualification. Per-campaign models stored in `Campaign.model_blob` (DB).
- **LLM integration**: Multi-provider via `conf.py:get_llm()` factory. `SiteConfig` DB singleton stores `llm_provider` (openai/gemini), `llm_api_key`, `ai_model` (default: `gemini-2.5-flash-lite`), `llm_api_base`. `get_llm_config()` returns 4-tuple. Three call sites: `ml/qualifier.py`, `agents/follow_up.py`, `pipeline/search_keywords.py`.
- **CRM web UI**: Custom views in `crm/views.py` (15+ pages). Templates at `templates/crm/` (Tailwind CSS CDN + HTMX + Chart.js). Replaces Django Admin as primary interface. Dashboard, leads, deals, campaigns, accounts (with daemon start/stop), tasks, activity log, settings.
- **Daemon manager**: `linkedin/daemon_manager.py` — thread-based daemon lifecycle per `LinkedInProfile`. `DaemonInfo` dataclass tracks state, thread, stop_event. Functions: `start_daemon()`, `stop_daemon()`, `get_all_daemons()`.
- **Lazy accessors**: `Lead.get_profile(session)`, `Lead.get_urn(session)`, `Lead.get_embedding(session)` — fetch from API and cache in DB on first access. Chained: `get_embedding` → `get_profile` → Voyager API. `Lead.to_profile_dict()` reads existing data only. `AccountSession.campaigns` (cached_property, list). `AccountSession.self_profile` (cached_property, reads from `LinkedInProfile.self_lead`, discovers via API on first run).
- **Django apps**: `linkedin` (main — Campaign, SiteConfig, LinkedInProfile, Task, ActionLog, SearchKeyword), `crm` (Lead with embedding/Deal, custom views/middleware/setup), `chat` (ChatMessage).
- **Data dir**: `data/` holds persistent state (`db.sqlite3`, `.premigrations`). Docker users mount volumes at `/app/data`.
- **Docker**: Playwright base image, VNC on port 5900, `BUILD_ENV` arg selects requirements.
- **CI/CD**: `.github/workflows/tests.yml` (pytest), `deploy.yml` (build + push to ghcr.io).
