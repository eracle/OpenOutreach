# Architecture

Detailed module documentation for OpenOutreach. See `CLAUDE.md` for rules and quick reference.

## Entry Flow

`manage.py` — stock Django management entrypoint. Bare `python manage.py` (no args) defaults to `rundaemon`.

### `rundaemon` management command (`management/commands/rundaemon.py`)

Startup sequence:
1. **Configure logging** — DEBUG level, suppresses noisy third-party loggers (urllib3, httpx, langchain, openai, playwright, etc.).
2. **Ensure DB** — `run_premigrations()` (filesystem migrations) → `migrate --no-input` + `setup_crm` (idempotent).
3. **Onboard** — checks `missing_keys()`; if incomplete: uses `--onboard <config.json>` (non-interactive), falls back to interactive wizard (TTY), or exits with clear error (no TTY).
4. **Validate** — `LLM_API_KEY`, active `LinkedInProfile`, at least one campaign.
5. **Session** — `get_or_create_session(profile)`, sets default campaign (first non-freemium).
6. **Newsletter** — GDPR override + `ensure_newsletter_subscription()` (marker-guarded, runs once).
7. **Run** — `run_daemon(session)`.

Docker `start` script handles only Xvfb/VNC setup, then `exec python manage.py rundaemon "$@"`.

### Web-based startup (CRM UI)

Alternative to terminal-based `rundaemon`. The CRM web UI can start/stop daemons per-profile:

1. User clicks "Start Daemon" on `/crm/accounts/` page.
2. `daemon_manager.start_daemon(profile_pk)` creates a `DaemonInfo` entry and spawns a background `threading.Thread`.
3. Thread creates an `AccountSession` via `get_or_create_session()`, validates LLM key + campaigns.
4. Thread calls `run_daemon(session, stop_event=info.stop_event)`.
5. `run_daemon()` checks `stop_event.is_set()` between tasks. When queue is empty, web-started daemons sleep 30s and re-check instead of exiting.
6. User clicks "Stop Daemon" → sets `stop_event` → daemon exits after current task.

Multiple profiles can run simultaneously, each in its own thread with its own browser.

### First-time setup flow

When no superuser exists in the database:
1. `FirstTimeSetupMiddleware` intercepts all requests and redirects to `/setup/`.
2. User creates admin account (username + password with confirmation).
3. User is auto-logged in and redirected to `/crm/`.
4. Middleware caches the check — never redirects again after a superuser exists.

### Other management commands

- `onboard` — standalone onboarding (interactive or `--non-interactive` with `--config-file` / individual flags).
- `setup_crm` — idempotent CRM bootstrap (default Site).
- `add_seeds` — add seed LinkedIn profile URLs to a campaign.

## Onboarding (`onboarding.py`)

`OnboardConfig` — pure dataclass with all onboarding fields. Two constructors:
- `OnboardConfig.from_json(path)` — from JSON file (cloud / non-interactive).
- `collect_from_wizard()` — interactive questionary wizard (needs TTY), only asks for `missing_keys()`.

Single write path: `apply(config)` — idempotent, creates missing Campaign, LinkedInProfile, env vars, and legal acceptance. Four components:

1. **Campaign** — name, product docs, objective, booking link, seed URLs. Creates `Campaign` with M2M user membership.
2. **LinkedInProfile** — email, password, newsletter, rate limits. Django username from email slug.
3. **LLM config** — `LLM_PROVIDER`, `LLM_API_KEY`, `AI_MODEL`, `LLM_API_BASE` → writes to `SiteConfig` singleton in DB.
4. **Legal notice** — per-account acceptance stored as `LinkedInProfile.legal_accepted`.

## Profile State Machine

`enums.py:ProfileState` (TextChoices) values ARE CRM stage names: QUALIFIED, READY_TO_CONNECT, PENDING, CONNECTED, COMPLETED, FAILED. Pre-Deal states: url_only (no profile_data), enriched (has profile_data). `Lead.disqualified=True` = permanent account-level exclusion. LLM rejections = FAILED Deals with "Disqualified" closing reason (campaign-scoped).

`crm/models/deal.py:ClosingReason` (TextChoices): COMPLETED, FAILED, DISQUALIFIED. Used by `Deal.closing_reason`.

## Task Queue

Persistent queue backed by `Task` model. Worker loop in `daemon.py`: `seconds_until_active()` guard pauses outside active hours/rest days → pop oldest due task → set campaign on session → RUNNING → dispatch via `_HANDLERS` dict → COMPLETED/FAILED. Failures captured by `failure_diagnostics()` context manager. `heal_tasks()` reconciles on startup.

When started via web UI with a `stop_event`, the loop checks `stop_event.is_set()` before each iteration and uses `stop_event.wait(seconds)` instead of `time.sleep()` for interruptible waiting. On empty queue, web-started daemons sleep 30s and re-check rather than exiting.

Three task types (handlers in `linkedin/tasks/`, signature: `handle_*(task, session, qualifiers)`):

1. **`handle_connect`** — Unified via `ConnectStrategy` dataclass. Regular: `find_candidate()` from `pools.py`; freemium: `find_freemium_candidate()`. Unreachable detection after `MAX_CONNECT_ATTEMPTS` (3).
2. **`handle_check_pending`** — Per-profile. Exponential backoff with jitter. On acceptance → enqueues `follow_up`.
3. **`handle_follow_up`** — Per-profile. Calls `run_follow_up_agent()` which returns a `FollowUpDecision` (structured output: `send_message`/`mark_completed`/`wait`). Handler executes the decision deterministically.

## Qualification ML Pipeline

GPR (sklearn, ConstantKernel * RBF) inside Pipeline(StandardScaler, GPR) with BALD active learning:

1. **Balance-driven selection** — n_negatives > n_positives → exploit (highest P); otherwise → explore (highest BALD).
2. **LLM decision** — All decisions via LLM (`qualify_lead.j2`). GP only for candidate selection and confidence gate.
3. **READY_TO_CONNECT gate** — P(f > 0.5) above `min_ready_to_connect_prob` (0.9) promotes QUALIFIED → READY_TO_CONNECT.

384-dim FastEmbed embeddings stored directly on Lead model, per-campaign GP models at `Campaign.model_blob` (BinaryField). Cold start returns None until >=2 labels of both classes.

## Django Apps

Three apps in `INSTALLED_APPS`:

- **`linkedin`** — Main app: Campaign (with users M2M), LinkedInProfile, SiteConfig, SearchKeyword, ActionLog, Task models. All automation logic.
- **`crm`** — Lead (with embedding) and Deal models (in `crm/models/lead.py` and `crm/models/deal.py`). Also defines `ClosingReason` enum. Custom CRM views, middleware, setup views.
- **`chat`** — `ChatMessage` model (GenericForeignKey to any object, content, owner, answer_to threading, topic).

## CRM Web UI

Professional web interface replacing Django Admin, built with Tailwind CSS CDN + HTMX + Chart.js. No build step required.

### Pages & Views (`crm/views.py`)

| Page | URL | Description |
|------|-----|-------------|
| Dashboard | `/crm/` | Stats cards, pipeline funnel, Chart.js activity chart, recent leads/deals, task status |
| Leads | `/crm/leads/` | Searchable lead list with status/campaign filters, HTMX live search |
| Lead Detail | `/crm/leads/<pk>/` | Profile card, enriched data, deals table, conversation view, disqualify toggle |
| Deals | `/crm/deals/` | Deal list with state/campaign filters |
| Deal Detail | `/crm/deals/<pk>/` | Deal info card, conversation thread |
| Campaigns | `/crm/campaigns/` | Campaign cards grid with deal/user counts |
| Campaign Detail | `/crm/campaigns/<pk>/` | Pipeline stats, campaign info, search keywords, deals table |
| Campaign Edit | `/crm/campaigns/<pk>/edit/` | Campaign settings form |
| Campaign Create | `/crm/campaigns/new/` | New campaign with profile assignment |
| Accounts | `/crm/accounts/` | LinkedIn profiles with daemon start/stop controls |
| Add Account | `/crm/accounts/add/` | New LinkedIn profile with credentials, limits, campaign assignment |
| Edit Profile | `/crm/accounts/<pk>/edit/` | Edit credentials, limits, active status, campaigns |
| Tasks | `/crm/tasks/` | Task list with status/type filters |
| Activity Log | `/crm/activity/` | Chronological action log |
| Settings | `/crm/settings/` | LLM provider/model/key config, LinkedIn profile overview |

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/crm/api/chart-data/` | GET | JSON data for dashboard Chart.js graphs |
| `/crm/api/daemon-status/` | GET | JSON daemon status for all profiles (polled by accounts page) |
| `/crm/accounts/<pk>/start/` | POST | Start daemon for a LinkedIn profile |
| `/crm/accounts/<pk>/stop/` | POST | Stop daemon for a LinkedIn profile |

### Middleware (`crm/middleware.py`)

- **`FirstTimeSetupMiddleware`** — redirects to `/setup/` if no superuser exists. Caches the check after first superuser is created. Exempt paths: `/setup/`, `/static/`, `/admin/`.

### Templates (`templates/crm/`)

Base layout (`base.html`) with responsive sidebar navigation. All pages extend base. HTMX partials in `partials/` subfolder for live search (leads, deals, tasks).

## CRM Data Model

- **SiteConfig** (`linkedin/models.py`) — Singleton (pk=1). `llm_provider` (openai/gemini), `llm_api_key`, `ai_model` (default: `gemini-2.5-flash-lite`), `llm_api_base`. Accessed via `SiteConfig.load()` / `conf.get_llm_config()`. Factory `conf.get_llm()` returns provider-appropriate LangChain chat model (`ChatGoogleGenerativeAI` for Gemini, `ChatOpenAI` for OpenAI).
- **Campaign** (`linkedin/models.py`) — `name` (unique), `users` (M2M to User), `product_docs`, `campaign_objective`, `booking_link`, `is_freemium`, `action_fraction`, `seed_public_ids` (JSONField).
- **LinkedInProfile** (`linkedin/models.py`) — 1:1 with User. `self_lead` FK to Lead (nullable, set on first self-profile discovery). Credentials, rate limits (`connect_daily_limit`, `connect_weekly_limit`, `follow_up_daily_limit`). Methods: `can_execute`/`record_action`/`mark_exhausted`. In-memory `_exhausted` dict for daily rate limit caching.
- **SearchKeyword** (`linkedin/models.py`) — FK to Campaign. `keyword`, `used`, `used_at`. Unique on `(campaign, keyword)`.
- **ActionLog** (`linkedin/models.py`) — FK to LinkedInProfile + Campaign. `action_type` (connect/follow_up), `created_at`. Composite index on `(linkedin_profile, action_type, created_at)`.
- **Lead** (`crm/models/lead.py`) — Per LinkedIn URL (`linkedin_url` = unique). `public_identifier` (derived from URL). `first_name`, `last_name`, `company_name`. `profile_data` = JSONField (parsed profile dict, nullable). `embedding` = 384-dim float32 BinaryField (nullable). `disqualified` = permanent exclusion. `embedding_array` property for numpy access. `get_labeled_arrays(campaign)` classmethod returns (X, y) for GP warm start. Labels: non-FAILED state → 1, FAILED+DISQUALIFIED → 0, other FAILED → skipped.
- **Deal** (`crm/models/deal.py`) — Per campaign (campaign-scoped via FK). `state` = CharField (ProfileState choices). `closing_reason` = CharField (ClosingReason choices: COMPLETED/FAILED/DISQUALIFIED). `reason` = qualification/failure reason. `connect_attempts` = retry count. `backoff_hours` = check_pending backoff. `creation_date`, `update_date`.
- **Task** (`linkedin/models.py`) — `task_type` (connect/check_pending/follow_up), `status` (pending/running/completed/failed), `scheduled_at`, `payload` (JSONField), `error`, `started_at`, `completed_at`. Composite index on `(status, scheduled_at)`.
- **ChatMessage** (`chat/models.py`) — GenericForeignKey to any object. `content`, `owner`, `answer_to` (self FK), `topic` (self FK), `recipients`, `to` (M2M to User).

## LLM Integration

Multi-provider support via `conf.py:get_llm()` factory:

- **`SiteConfig.llm_provider`** — `"openai"` or `"gemini"` (default: `"gemini"`).
- **Gemini** — uses `langchain-google-genai`'s `ChatGoogleGenerativeAI`. Default model: `gemini-2.5-flash-lite`.
- **OpenAI** — uses `langchain-openai`'s `ChatOpenAI`. Supports custom `llm_api_base` for compatible endpoints.
- **`get_llm_config()`** — returns 4-tuple: `(provider, key, model, base)` from DB singleton.
- **Call sites** — `ml/qualifier.py` (qualification), `agents/follow_up.py` (follow-up decisions), `pipeline/search_keywords.py` (keyword generation). All use `get_llm()` with Pydantic structured output.

## Key Modules

- **`daemon.py`** — Worker loop with active-hours guard (`ENABLE_ACTIVE_HOURS` flag, `seconds_until_active()`), `_build_qualifiers()`, `heal_tasks()`, freemium import, `_FreemiumRotator`. Supports optional `stop_event` for graceful web-triggered shutdown.
- **`daemon_manager.py`** — Thread-based daemon lifecycle per LinkedInProfile. `DaemonInfo` dataclass tracks state (stopped/starting/running/stopping/error), thread, stop event, started_at, error. Module-level `_daemons` registry. Functions: `start_daemon()`, `stop_daemon()`, `get_all_daemons()`, `is_running()`.
- **`diagnostics.py`** — `failure_diagnostics()` context manager, `capture_failure()` saves page HTML/screenshot/traceback to `/tmp/openoutreach-diagnostics/`.
- **`tasks/connect.py`** — `handle_connect`, `ConnectStrategy`, `enqueue_connect`/`enqueue_check_pending`/`enqueue_follow_up`.
- **`tasks/check_pending.py`** — `handle_check_pending`, exponential backoff.
- **`tasks/follow_up.py`** — `handle_follow_up`, rate limiting.
- **`pipeline/qualify.py`** — `run_qualification()`, `fetch_qualification_candidates()`.
- **`pipeline/search.py`** — `run_search()`, keyword management.
- **`pipeline/search_keywords.py`** — `generate_search_keywords()` via LLM.
- **`pipeline/ready_pool.py`** — GP confidence gate, `promote_to_ready()`.
- **`pipeline/pools.py`** — Composable generators: `search_source` → `qualify_source` → `ready_source`.
- **`pipeline/freemium_pool.py`** — Seed priority + undiscovered pool, ranked by qualifier.
- **`ml/qualifier.py`** — `Qualifier` protocol, `BayesianQualifier`, `KitQualifier`, `qualify_with_llm()`.
- **`ml/embeddings.py`** — FastEmbed utilities, `embed_text()`, `embed_texts()`.
- **`ml/profile_text.py`** — `build_profile_text()`.
- **`ml/hub.py`** — HuggingFace kit loader (`fetch_kit()`).
- **`browser/session.py`** — `AccountSession`: linkedin_profile, page, context, browser, playwright. `campaigns` cached_property (list, via Campaign.users M2M). `ensure_browser()` launches/recovers browser. `self_profile` cached_property (reads from `self_lead`, discovers via API on first run). Cookie expiry check via `_maybe_refresh_cookies()`.
- **`browser/registry.py`** — `get_or_create_session()`, `get_first_active_profile()`, `resolve_profile()`, `cli_parser()`/`cli_session()` (shared CLI bootstrap for `__main__` scripts).
- **`browser/login.py`** — `start_browser_session()` — browser launch + LinkedIn login.
- **`browser/nav.py`** — Navigation, auto-discovery, `goto_page()`.
- **`db/leads.py`** — Lead CRUD, `get_leads_for_qualification()`, `disqualify_lead()`.
- **`db/deals.py`** — Deal/state ops, `set_profile_state()`, `increment_connect_attempts()`, `create_freemium_deal()`.
- **`db/chat.py`** — `save_chat_message()`.
- **`url_utils.py`** — `url_to_public_id()`, `public_id_to_url()` — LinkedIn URL ↔ public identifier conversion. Pure utility, no DB dependency.
- **`conf.py`** — Config constants, `CAMPAIGN_CONFIG`, `get_llm_config()` (reads from `SiteConfig` in DB, returns 4-tuple), `get_llm()` (factory returning provider-specific LangChain chat model).
- **`exceptions.py`** — `AuthenticationError`, `TerminalStateError`, `SkipProfile`, `ReachedConnectionLimit`.
- **`onboarding.py`** — Interactive setup.
- **`agents/follow_up.py`** — Follow-up agent. Single LLM call with structured output (`FollowUpDecision`). Conversation is read in Python and injected into the prompt. No tool-calling loop.
- **`actions/`** — `connect.py` (`send_connection_request`), `status.py` (`get_connection_status`), `message.py` (`send_raw_message`), `profile.py` (profile extraction), `search.py` (LinkedIn search), `conversations.py` (`get_conversation`).
- **`api/client.py`** — `PlaywrightLinkedinAPI`: browser-context fetch (runs JS `fetch()` inside Playwright page for authentic headers). `timeout_ms` constructor param (default 30s). `get_profile()` with tenacity retry.
- **`api/voyager.py`** — `LinkedInProfile` dataclass (url, urn, full_name, headline, positions, educations, country_code, supported_locales, connection_distance/degree). `parse_linkedin_voyager_response()`.
- **`api/newsletter.py`** — `subscribe_to_newsletter()` via Brevo form, `ensure_newsletter_subscription()`. No config parsing — subscribe_newsletter is a BooleanField.
- **`api/messaging/send.py`** — Send messages via Voyager messaging API.
- **`api/messaging/conversations.py`** — Fetch conversations/messages.
- **`api/messaging/utils.py`** — Shared helpers: `encode_urn()`, `check_response()`.
- **`setup/freemium.py`** — `import_freemium_campaign()`, `seed_profiles()`.
- **`setup/gdpr.py`** — `apply_gdpr_newsletter_override()`.
- **`setup/self_profile.py`** — `discover_self_profile()` — fetches self profile via Voyager API, sets `linkedin_profile.self_lead`.
- **`setup/seeds.py`** — User-provided seed profiles: parse URLs, create Leads + QUALIFIED Deals.
- **`management/setup_crm.py`** — Idempotent CRM bootstrap (Site creation).
- **`admin.py`** — Django Admin: SiteConfig, Campaign, LinkedInProfile, SearchKeyword, ActionLog, Task, ChatMessage.
- **`django_settings.py`** — Django settings (SQLite at `data/db.sqlite3`). Apps: crm, chat, linkedin. Includes `FirstTimeSetupMiddleware`.
- **`premigrations/`** — Pre-Django filesystem migrations. Numbered `NNNN_*.py` files with `forward(root_dir)` functions. Runner in `__init__.py` discovers and applies unapplied migrations, tracked via `data/.premigrations` JSON file.

## Configuration

- **`SiteConfig`** (DB singleton) — `llm_provider` (openai/gemini, default: gemini), `llm_api_key` (required), `ai_model` (default: `gemini-2.5-flash-lite`), `llm_api_base` (optional). Editable via Django Admin or CRM Settings page.
- **`conf.py` schedule** — `ACTIVE_START_HOUR` (9), `ACTIVE_END_HOUR` (17), `ACTIVE_TIMEZONE` ("UTC"), `REST_DAYS` ((5, 6) = Sat+Sun). Daemon sleeps outside this window.
- **`conf.py:CAMPAIGN_CONFIG`** — `min_ready_to_connect_prob` (0.9), `min_positive_pool_prob` (0.20), `connect_delay_seconds` (10), `connect_no_candidate_delay_seconds` (300), `check_pending_recheck_after_hours` (24), `check_pending_jitter_factor` (0.2), `qualification_n_mc_samples` (100), `enrich_min_interval` (1), `min_action_interval` (120), `embedding_model` ("BAAI/bge-small-en-v1.5").
- **Prompt templates** (at `linkedin/templates/prompts/`) — `qualify_lead.j2` (temp 0.7), `search_keywords.j2` (temp 0.9), `follow_up_agent.j2`.
- **`requirements/`** — `base.txt` (includes `langchain-google-genai`), `local.txt`, `production.txt`, `crm.txt`.

## Docker

Base image: `mcr.microsoft.com/playwright/python:v1.55.0-noble`. VNC on port 5900. `BUILD_ENV` arg selects requirements. Dockerfile at `compose/linkedin/Dockerfile`. Install: uv pip → DjangoCRM `--no-deps` → requirements → Playwright chromium.

## CI/CD

- `tests.yml` — pytest in Docker on push to `master` and PRs.
- `deploy.yml` — Tests → build + push to `ghcr.io/eracle/openoutreach`. Tags: `latest`, `sha-<commit>`, semver.

## Dependencies

`requirements/` files. DjangoCRM's `mysqlclient` excluded via `--no-deps`. `uv pip install` for fast installs.

Core: `playwright`, `playwright-stealth`, `Django`, `django-crm-admin`, `pandas`, `langchain`/`langchain-openai`, `jinja2`, `pydantic`, `jsonpath-ng`, `tendo`, `termcolor`, `tenacity`
ML: `scikit-learn`, `numpy`, `fastembed`, `joblib`
