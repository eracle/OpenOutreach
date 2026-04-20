# Architecture

Detailed module documentation for OpenOutreach. See `CLAUDE.md` for rules and quick reference.

## Entry Flow

`manage.py` — stock Django management entrypoint. Bare `python manage.py` (no args) defaults to `rundaemon`.

### `rundaemon` management command (`management/commands/rundaemon.py`)

Startup sequence:
1. **Configure logging** — DEBUG level, suppresses noisy third-party loggers (urllib3, httpx, langchain, openai, playwright, etc.).
2. **Ensure DB** — `migrate --no-input` + `setup_crm` (idempotent).
3. **Onboard** — checks `missing_keys()`; if incomplete: uses `--onboard <config.json>` (non-interactive), falls back to interactive wizard (TTY), or exits with clear error (no TTY).
4. **Validate** — `LLM_API_KEY`, active `LinkedInProfile`, at least one campaign.
5. **Session** — `get_or_create_session(profile)`, sets default campaign (first non-freemium).
6. **Newsletter** — GDPR override + `ensure_newsletter_subscription()` (marker-guarded, runs once).
7. **Run** — `run_daemon(session)`.

Docker `start` script handles only Xvfb/VNC setup, then `exec python manage.py rundaemon "$@"`.

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
3. **LLM config** — `LLM_API_KEY`, `AI_MODEL`, `LLM_API_BASE` → writes to `SiteConfig` singleton in DB.
4. **Legal notice** — per-account acceptance stored as `LinkedInProfile.legal_accepted`.

## Profile State Machine

`enums.py:ProfileState` (TextChoices) values ARE CRM stage names: QUALIFIED, READY_TO_CONNECT, PENDING, CONNECTED, COMPLETED, FAILED. Pre-Deal states: url_only (Lead row exists but `embedding` is null), enriched (has `embedding`). `Lead.disqualified=True` = permanent account-level exclusion. LLM rejections = FAILED Deals with wrong_fit outcome (campaign-scoped).

`crm/models/deal.py:Outcome` (TextChoices): converted, not_interested, wrong_fit, no_budget, has_solution, bad_timing, unresponsive, unknown. Used by `Deal.outcome`.

## Task Queue

Persistent queue backed by `Task` model. Worker loop in `daemon.py`: `seconds_until_active()` guard pauses outside active hours/rest days → pop oldest due task → set campaign on session → RUNNING → dispatch via `_HANDLERS` dict → COMPLETED/FAILED. Failures captured by `failure_diagnostics()` context manager.

Task creation is centralized in `linkedin/tasks/scheduler.py`. No other module inserts Task rows. The module exposes three layers: (1) low-level `enqueue_connect`/`enqueue_check_pending`/`enqueue_follow_up` with per-call dedup against existing PENDING rows, (2) a state-transition hook `on_deal_state_entered(deal)` fired by `set_profile_state()` that picks the right task for the new state, and (3) `reconcile(session)` which walks CRM state and recreates missing tasks.

The daemon calls `reconcile()` whenever the queue has no ready task — startup and every idle cycle. This is the retry mechanism: a handler that crashes mid-execution leaves a FAILED task with no successor, and the next idle cycle re-creates it from the Deal's state. `AuthenticationError` (401) triggers `session.reauthenticate()` then marks the task FAILED; reconcile picks it up.

Three task types (handlers in `linkedin/tasks/`, signature: `handle_*(task, session, qualifiers)`):

1. **`handle_connect`** — Unified via `ConnectStrategy` dataclass. Regular: `find_candidate()` from `pools.py`; freemium: `find_freemium_candidate()`. Unreachable detection after `MAX_CONNECT_ATTEMPTS` (3).
2. **`handle_check_pending`** — Per-profile. Exponential backoff with jitter. On acceptance → enqueues `follow_up`.
3. **`handle_follow_up`** — Per-profile. Calls `run_follow_up_agent()` which returns a `FollowUpDecision` (structured output: `send_message`/`mark_completed`/`wait`). Handler executes the decision deterministically.

## Qualification ML Pipeline

GPR (sklearn, ConstantKernel * RBF) inside Pipeline(StandardScaler, GPR) with BALD active learning:

1. **Balance-driven selection** — n_negatives > n_positives → exploit (highest P); otherwise → explore (highest BALD).
2. **LLM decision** — All decisions via LLM (`qualify_lead.j2`). GP only for candidate selection and confidence gate.
3. **READY_TO_CONNECT gate** — P(f > 0.5) above `min_ready_to_connect_prob` (0.9) promotes QUALIFIED → READY_TO_CONNECT.

384-dim FastEmbed embeddings stored directly on Lead model, per-campaign GP models at ``Campaign.model_blob` (BinaryField, joblib-dumped with `compress=3`)`. Cold start returns None until >=2 labels of both classes.

## Django Apps

Three apps in `INSTALLED_APPS`:

- **`linkedin`** — Main app: Campaign (with users M2M), LinkedInProfile, SearchKeyword, ActionLog, Task models. All automation logic.
- **`crm`** — Lead (with embedding) and Deal models (in `crm/models/lead.py` and `crm/models/deal.py`). Also defines `Outcome` enum.
- **`chat`** — `ChatMessage` model (GenericForeignKey to any object, content, owner, answer_to threading, topic).

## CRM Data Model

- **SiteConfig** (`linkedin/models.py`) — Singleton (pk=1). `llm_api_key`, `ai_model`, `llm_api_base`. Accessed via `SiteConfig.load()` / `conf.get_llm_config()`.
- **Campaign** (`linkedin/models.py`) — `name` (unique), `users` (M2M to User), `product_docs`, `campaign_objective`, `booking_link`, `is_freemium`, `action_fraction`, `seed_public_ids` (JSONField).
- **LinkedInProfile** (`linkedin/models.py`) — 1:1 with User. `self_lead` FK to Lead (nullable, set on first self-profile discovery). Credentials, rate limits (`connect_daily_limit`, `connect_weekly_limit`, `follow_up_daily_limit`). Methods: `can_execute`/`record_action`/`mark_exhausted`. In-memory `_exhausted` dict for daily rate limit caching.
- **SearchKeyword** (`linkedin/models.py`) — FK to Campaign. `keyword`, `used`, `used_at`. Unique on `(campaign, keyword)`.
- **ActionLog** (`linkedin/models.py`) — FK to LinkedInProfile + Campaign. `action_type` (connect/follow_up), `created_at`. Composite index on `(linkedin_profile, action_type, created_at)`.
- **Lead** (`crm/models/lead.py`) — Per LinkedIn URL (`linkedin_url` = unique). `public_identifier` (derived from URL, unique). `urn` = unique CharField (LinkedIn entity URN, cached on first scrape). `embedding` = 384-dim float32 BinaryField (nullable). `disqualified` = permanent exclusion. The parsed profile dict, person name, and company name are **not stored** — they live only in memory for the lifetime of a scrape dict. Callers that need them re-scrape via `lead.get_profile(session)`. `embedding_array` property for numpy access. `embed_from_profile(profile)` computes + persists the embedding from an in-hand dict (skips the scrape). `get_labeled_arrays(campaign)` classmethod returns (X, y) for GP warm start. Labels: non-FAILED state → 1, FAILED+wrong_fit → 0, other FAILED → skipped.
- **Deal** (`crm/models/deal.py`) — Per campaign (campaign-scoped via FK). `state` = CharField (ProfileState choices). `outcome` = CharField (Outcome choices: converted/not_interested/wrong_fit/no_budget/has_solution/bad_timing/unresponsive/unknown). `reason` = qualification reason (free text). `connect_attempts` = retry count. `backoff_hours` = check_pending backoff. `profile_summary` / `chat_summary` = JSONField fact lists (lazy, mem0-style, campaign-scoped). `creation_date`, `update_date`.
- **Task** (`linkedin/models.py`) — `task_type` (connect/check_pending/follow_up), `status` (pending/running/completed/failed), `scheduled_at`, `payload` (JSONField), `error`, `started_at`, `completed_at`. Composite index on `(status, scheduled_at)`.
- **ChatMessage** (`chat/models.py`) — GenericForeignKey to any object. `content`, `owner`, `answer_to` (self FK), `topic` (self FK), `recipients`, `to` (M2M to User).

## Key Modules

- **`daemon.py`** — Worker loop with active-hours guard (`ENABLE_ACTIVE_HOURS` flag, `seconds_until_active()`), `_build_qualifiers()`, freemium import, `_CloudPromoRotator`. Calls `scheduler.reconcile()` when the queue has no ready task.
- **`diagnostics.py`** — `failure_diagnostics()` context manager, `capture_failure()` saves page HTML/screenshot/traceback to `/tmp/openoutreach-diagnostics/`.
- **`tasks/scheduler.py`** — Single owner of Task row creation. Low-level `enqueue_*`, state-transition hook `on_deal_state_entered`, and `reconcile()`.
- **`tasks/connect.py`** — `handle_connect`, `ConnectStrategy`.
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
- **`browser/session.py`** — `AccountSession`: linkedin_profile, page, context, browser, playwright. `campaigns` cached_property (list, via Campaign.users M2M). `ensure_browser()` launches/recovers browser. `self_profile` cached_property (re-discovers via Voyager on first access per session — no DB cache; one extra scrape per daemon restart). Cookie expiry check via `_maybe_refresh_cookies()`. `reauthenticate()` forces fresh login (close browser, clear saved cookies, re-launch).
- **`browser/registry.py`** — `get_or_create_session()`, `get_first_active_profile()`, `resolve_profile()`, `cli_parser()`/`cli_session()` (shared CLI bootstrap for `__main__` scripts).
- **`browser/login.py`** — `start_browser_session()` — browser launch + LinkedIn login.
- **`browser/nav.py`** — Navigation, auto-discovery, `goto_page()`.
- **`db/leads.py`** — Lead CRUD, `get_leads_for_qualification()`, `disqualify_lead()`, `_cache_urn_from_profile()`.
- **`db/deals.py`** — Deal/state ops, `set_profile_state()`, `increment_connect_attempts()`, `create_freemium_deal()`.
- **`db/chat.py`** — `sync_conversation()`, `_sync_from_api()`, folds newly-synced messages into `Deal.chat_summary` via `update_chat_summary`.
- **`db/summaries.py`** — Single mem0-style LLM boundary. `materialize_profile_summary_if_missing(deal, session)` fires on first follow-up touch (one Voyager re-scrape per `(lead, campaign)` lifetime); `update_chat_summary(deal, new_messages)` folds newly-synced ChatMessages incrementally via `reconcile_facts`, which routes new facts through mem0's UPDATE prompt to apply ADD/UPDATE/DELETE/NONE events (mirrors `mem0/memory/main.py::Memory._add_to_vector_store` lines 594-700, with vector-store ops replaced by an in-memory dict because `Deal.chat_summary` is a flat list). `_format_messages_for_extraction` filters to incoming messages only, so `chat_summary` holds facts about the lead and a one-sided outgoing burst is a noop. `extract_facts(text, context)` runs `ChatOpenAI.with_structured_output(FactList)` against the vendored `_FACT_EXTRACTION_PROMPT`. mem0's `DEFAULT_UPDATE_MEMORY_PROMPT` and `get_update_memory_messages` live under `linkedin/vendor/mem0/configs/prompts.py` (mirrors upstream path so future syncs are a clean diff; pinned commit recorded in the file header).
- **`url_utils.py`** — `url_to_public_id()`, `public_id_to_url()` — LinkedIn URL ↔ public identifier conversion. Pure utility, no DB dependency.
- **`conf.py`** — Config constants, `CAMPAIGN_CONFIG`, `get_llm_config()` (reads from `SiteConfig` in DB).
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
- **`django_settings.py`** — Django settings (SQLite at `data/db.sqlite3`). Apps: crm, chat, linkedin.


## Configuration

- **`SiteConfig`** (DB singleton) — `llm_api_key` (required), `ai_model` (required), `llm_api_base` (optional). Editable via Django Admin.
- **`conf.py` schedule** — `ENABLE_ACTIVE_HOURS` (`True`), `ACTIVE_START_HOUR` (9), `ACTIVE_END_HOUR` (19), `ACTIVE_TIMEZONE` (system-local IANA name, falls back to "UTC"), `REST_DAYS` ((5, 6) = Sat+Sun). Daemon sleeps outside this window.
- **`conf.py:CAMPAIGN_CONFIG`** — `min_ready_to_connect_prob` (0.9), `min_positive_pool_prob` (0.20), `connect_delay_seconds` (10), `connect_no_candidate_delay_seconds` (300), `check_pending_recheck_after_hours` (24), `check_pending_jitter_factor` (0.2), `qualification_n_mc_samples` (100), `enrich_min_delay_seconds` (6), `enrich_max_delay_seconds` (10), `enrich_max_per_page` (10), `burst_min_seconds` (2700), `burst_max_seconds` (3900), `break_min_seconds` (600), `break_max_seconds` (1200), `min_action_interval` (120), `embedding_model` ("BAAI/bge-small-en-v1.5").
- **Prompt templates** (at `linkedin/templates/prompts/`) — `qualify_lead.j2` (temp 0.7), `search_keywords.j2` (temp 0.9), `follow_up_agent.j2`.
- **`requirements/`** — `base.txt`, `local.txt`, `production.txt`, `crm.txt` (empty — DjangoCRM installed via `--no-deps`).

## Docker

Base image: `mcr.microsoft.com/playwright/python:v1.55.0-noble`. VNC on port 5900. `BUILD_ENV` arg selects requirements. Dockerfile at `compose/linkedin/Dockerfile`. Install: uv pip → DjangoCRM `--no-deps` → requirements → Playwright chromium.

## CI/CD

- `tests.yml` — pytest in Docker on push to `master` and PRs.
- `deploy.yml` — Tests → build + push to `ghcr.io/eracle/openoutreach`. Tags: `latest`, `sha-<commit>`, semver.

## Dependencies

`requirements/` files. DjangoCRM's `mysqlclient` excluded via `--no-deps`. `uv pip install` for fast installs.

Core: `playwright`, `playwright-stealth`, `Django`, `django-crm-admin`, `pandas`, `langchain`/`langchain-openai`, `jinja2`, `pydantic`, `jsonpath-ng`, `tendo`, `termcolor`, `tenacity`
ML: `scikit-learn`, `numpy`, `fastembed`, `joblib`
