# Architecture

Detailed module documentation for OpenOutreach. See `CLAUDE.md` for rules and quick reference.

## Project Layout

All source lives in the single `openoutreach/` package; Django apps are nested inside it
(dotted `AppConfig.name`, short labels). One engine, N outreach channels:

```
manage.py
tests/
openoutreach/
  settings.py        # Django settings (was linkedin/django_settings.py)
  urls.py
  core/              # engine app (label: core) — daemon, task queue + scheduler,
                     #   Campaign/SiteConfig/Task models, llm.py, conf.py, onboarding,
                     #   follow-up agent, db/ helpers, management commands, vendored mem0
  linkedin/          # channel app (label: linkedin) — browser/, pipeline/, ml/,
                     #   LinkedInProfile/SearchKeyword/ActionLog models, task handlers, setup/
  emails/            # channel app (label: emails) — email outreach (finder, sender; Layer 1 WIP)
  crm/               # app (label: crm) — Lead, Deal
  chat/              # app (label: chat) — ChatMessage
```

Layering: `core` owns orchestration and channel-agnostic models; channel apps own their
platform mechanics, channel-bound models, and task handlers. `core` imports channel code
only at wiring points (the daemon's handler map, onboarding's profile setup).

## Entry Flow

`manage.py` — stock Django management entrypoint. Bare `python manage.py` (no args) defaults to `rundaemon`.

### `rundaemon` management command (`management/commands/rundaemon.py`)

Startup sequence:
1. **Configure logging** — DEBUG level, suppresses noisy third-party loggers (urllib3, httpx, pydantic_ai, openai, playwright, etc.).
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
- `collect_from_wizard()` — interactive questionary wizard (needs TTY), only asks for `missing_keys()`. Backed by the vendored `onboarding_wizard.py` (step engine) + `onboarding_prompts.py` (`SELF_HOSTED_QUESTIONS`) — no external `openoutreach` dependency.

Single write path: `apply(config)` — idempotent, creates missing Campaign, LinkedInProfile, env vars, and legal acceptance. Four components:

1. **Campaign** — name, product docs, objective, booking link, seed URLs. Creates `Campaign` with M2M user membership.
2. **LinkedInProfile** — email, password, newsletter, rate limits. Django username from email slug.
3. **LLM config** — `LLM_PROVIDER`, `LLM_API_KEY`, `AI_MODEL`, `LLM_API_BASE` → writes to `SiteConfig` singleton in DB.
4. **Legal notice** — per-account acceptance stored as `LinkedInProfile.legal_accepted`.

## Profile State Machine

`enums.py:ProfileState` (TextChoices) values ARE CRM stage names: QUALIFIED, READY_TO_CONNECT, PENDING, CONNECTED, COMPLETED, FAILED. Pre-Deal states: url_only (Lead row exists but `embedding` is null), enriched (has `embedding`). `Lead.disqualified=True` = permanent account-level exclusion. LLM rejections = FAILED Deals with wrong_fit outcome (campaign-scoped).

`crm/models/deal.py:Outcome` (TextChoices): converted, not_interested, wrong_fit, no_budget, has_solution, bad_timing, unresponsive, unknown. Used by `Deal.outcome`.

## Task Queue

Persistent queue backed by `Task` model. Worker loop in `daemon.py`: `seconds_until_active()` guard pauses outside the daily active-hours window (single contiguous window, no weekend skip) → pop oldest due task → set campaign on session → RUNNING → dispatch via `_HANDLERS` dict → COMPLETED/FAILED. Failures captured by `failure_diagnostics()` context manager.

Task rows are **lazy**: `payload = {"campaign_id": <id>}` only — no `public_id`, no deal reference. The handler resolves a concrete target at execution time via a single eligibility query. Slot creation is centralized in `openoutreach/core/scheduler.py`; no other module inserts Task rows. The module is organized in three layers:

1. **Per-type planners** — `plan_connect_window`, `plan_follow_up_window`, `plan_check_pending_window`. Each, when no PENDING task of its type exists for a campaign, computes the right slot count `n` for the next 24h and inserts `1 immediate + (n-1) Poisson-spaced` lazy rows. The leading immediate slot kills the cold-start ramp (without it the first action would sit `T/n` away on average — ~72 min for a 20/day campaign).
2. **State-transition hook** — `on_deal_state_entered(deal)`. For PENDING transitions, stamps `deal.next_check_pending_at = now + backoff_hours`. All other transitions are no-ops. Separately, `set_profile_state` itself fires a best-effort `Lead.capture_contact_info(session)` on the first CONNECTED transition (contact-info overlay scrape — the hook can't, it has no `session`); errors are logged and never fail the transition, `AuthenticationError` propagates.
3. **`reconcile(session)`** — Recovers stale RUNNING tasks, then iterates campaigns × planners. Daemon calls it on startup and whenever the queue has no ready task.

Per-type recompute trigger: when a type's PENDING queue is empty for a campaign, the next idle reconcile re-plans only that type's next 24h window. No global rollover, no leftover-slot reconciliation. `AuthenticationError` (401) triggers `session.reauthenticate()` then marks the task FAILED; the planner picks the type back up on the next idle cycle.

Three task types (handlers in `openoutreach/linkedin/tasks/`, signature: `handle_*(task, session, qualifiers)`):

1. **`handle_connect`** — Unified via `ConnectStrategy` dataclass. Regular: `find_candidate()` from `pools.py`; freemium: `find_freemium_candidate()`. Unreachable detection after `MAX_CONNECT_ATTEMPTS` (3). No self-rescheduling — the planner owns timing.
2. **`handle_check_pending`** — Eligibility query: oldest PENDING deal in the campaign with `next_check_pending_at <= now`. If none, mark task DONE. On still-PENDING outcome, double `backoff_hours` and re-stamp `next_check_pending_at`.
3. **`handle_follow_up`** — Eligibility query: oldest CONNECTED deal in the campaign with no recent outgoing message. If none, mark task DONE. Otherwise call `run_follow_up_agent()` (returns `FollowUpDecision`: `send_message`/`mark_completed`/`wait`) and execute deterministically.

## Qualification ML Pipeline

GPR (sklearn, ConstantKernel * RBF) inside Pipeline(StandardScaler, GPR) with BALD active learning:

1. **Balance-driven selection** — n_negatives > n_positives → exploit (highest P); otherwise → explore (highest BALD).
2. **LLM decision** — All decisions via LLM (`qualify_lead.j2`). GP only for candidate selection and confidence gate.
3. **READY_TO_CONNECT gate** — P(f > 0.5) above `min_ready_to_connect_prob` (0.9) promotes QUALIFIED → READY_TO_CONNECT.

384-dim FastEmbed embeddings stored directly on Lead model, per-campaign GP models at ``Campaign.model_blob` (BinaryField, joblib-dumped with `compress=3`)`. Cold start returns None until >=2 labels of both classes.

## Django Apps

Five apps in `INSTALLED_APPS`, all nested under the `openoutreach/` package (see Project Layout):

- **`core`** — Engine: SiteConfig, Campaign (with users M2M), Task models; daemon, scheduler, LLM factory, onboarding, follow-up agent.
- **`linkedin`** — LinkedIn channel: LinkedInProfile, SearchKeyword, ActionLog models; browser, discovery pipeline, ML qualification, task handlers.
- **`emails`** — Email channel (Layer 1 of the email-first pivot). `finder.py` resolves a work email for a qualified lead on demand (`resolve_email`); `bettercontact.py` is the one provider (submit→poll over the BetterContact API). Sender + tasks land in later slices.
- **`crm`** — Lead (with embedding) and Deal models (in `crm/models/lead.py` and `crm/models/deal.py`). Also defines `Outcome` enum.
- **`chat`** — `ChatMessage` model (GenericForeignKey to any object, content, owner, answer_to threading, topic).

History note: core's models lived in the linkedin app until mid-2026; the move was state-only
plus table renames (`linkedin_campaign` → `core_campaign` etc., `core.0002_rename_engine_tables`).

## CRM Data Model

- **SiteConfig** (`core/models.py`) — Singleton (pk=1). `llm_provider` (TextChoices: openai/anthropic/google/groq/mistral/cohere/openai_compatible), `llm_api_key`, `ai_model`, `llm_api_base`, `finder_api_key` (BetterContact email-finder key; blank disables enrichment — see `emails/finder.py`). Accessed via `SiteConfig.load()`; `core/llm.py:get_llm_model()` is the single factory that turns it into a `pydantic_ai.models.Model`.
- **Campaign** (`core/models.py`) — `name` (unique), `users` (M2M to User), `product_docs`, `campaign_objective`, `booking_link`, `is_freemium`, `action_fraction`, `seed_public_ids` (JSONField).
- **LinkedInProfile** (`linkedin/models.py`) — 1:1 with User. `self_lead` FK to Lead (nullable, set on first self-profile discovery). Credentials, rate limits (`connect_daily_limit`, `follow_up_daily_limit` — daily-only; LinkedIn's own weekly ceiling surfaces at the handler boundary via `ReachedConnectionLimit`). Methods: `can_execute`/`record_action`/`mark_exhausted`. In-memory `_exhausted` dict for daily rate limit caching.
- **SearchKeyword** (`linkedin/models.py`) — FK to Campaign. `keyword`, `used`, `used_at`. Unique on `(campaign, keyword)`.
- **ActionLog** (`linkedin/models.py`) — FK to LinkedInProfile + Campaign. `action_type` (connect/follow_up), `created_at`. Composite index on `(linkedin_profile, action_type, created_at)`.
- **Lead** (`crm/models/lead.py`) — Per LinkedIn URL (`linkedin_url` = unique). `public_identifier` (derived from URL, unique). `urn` = unique CharField (LinkedIn entity URN, cached on first scrape). `embedding` = 384-dim float32 BinaryField (nullable). `disqualified` = permanent exclusion. **Email storage = one field per source:** `contact_info` (nullable JSON — raw LinkedIn contact-info overlay `{email, emails, phone_numbers}`, captured once at CONNECTED; null = never scraped, the idempotency flag) and `api_email` (nullable EmailField — enrichment-API result; null = not found). `resolve_api_email()` populates it via the finder at qualification (tri-state: True hit / False genuine-miss / None finder-unavailable; cached on a hit, a miss is free to retry). A genuine miss is meant to park the Deal in the OpenOutreach-owned `NO_EMAIL` state (held out of the connect pool) — not yet wired. The parsed profile dict, person name, and company name are **not stored** — they live only in memory for the lifetime of a scrape dict. Callers that need them re-scrape via `lead.get_profile(session)`. `embedding_array` property for numpy access. `embed_from_profile(profile)` computes + persists the embedding from an in-hand dict (skips the scrape). `get_labeled_arrays(campaign)` classmethod returns (X, y) for GP warm start. Labels: non-FAILED state → 1, FAILED+wrong_fit → 0, other FAILED → skipped.
- **Deal** (`crm/models/deal.py`) — Per campaign (campaign-scoped via FK). `state` = CharField (ProfileState choices). `outcome` = CharField (Outcome choices: converted/not_interested/wrong_fit/no_budget/has_solution/bad_timing/unresponsive/unknown). `reason` = qualification reason (free text). `connect_attempts` = retry count. `backoff_hours` = check_pending backoff. `next_check_pending_at` = DateTimeField (indexed) stamped by `on_deal_state_entered(PENDING)`; the `check_pending` eligibility query and `plan_check_pending_window` both read it. `profile_summary` / `chat_summary` = JSONField fact lists (lazy, mem0-style, campaign-scoped). `creation_date`, `update_date`.
- **Task** (`core/models.py`) — `task_type` (connect/check_pending/follow_up), `status` (pending/running/completed/failed), `scheduled_at`, `payload` (JSONField), `started_at`, `completed_at`. Composite index on `(status, scheduled_at)`.
- **ChatMessage** (`chat/models.py`) — GenericForeignKey to any object. `content`, `owner`, `answer_to` (self FK), `topic` (self FK), `recipients`, `to` (M2M to User).

## Key Modules

Paths below are relative to `openoutreach/`.

- **`core/daemon.py`** — Worker loop with active-hours guard (`ENABLE_ACTIVE_HOURS` flag, `seconds_until_active()`), `_build_qualifiers()`, freemium import, `_CloudPromoRotator`. Calls `scheduler.reconcile()` when the queue has no ready task.
- **`linkedin/diagnostics.py`** — `failure_diagnostics()` context manager, `capture_failure()` saves page HTML/screenshot/traceback to `/tmp/openoutreach-diagnostics/`.
- **`core/scheduler.py`** — Single owner of Task row creation. Per-type planners (`plan_connect_window` / `plan_follow_up_window` / `plan_check_pending_window`) emit lazy slots with `1 immediate + (n-1) Poisson-spaced`; `poisson_slot_times(now, n, horizon_hours)` + `working_seconds_in_window(start, end)` are the spacing primitives. State-transition hook `on_deal_state_entered` only stamps `Deal.next_check_pending_at` for PENDING. `reconcile()` recovers stale RUNNING + dispatches the per-type planners.
- **`linkedin/tasks/connect.py`** — `handle_connect`, `ConnectStrategy`.
- **`linkedin/tasks/check_pending.py`** — `handle_check_pending`, exponential backoff.
- **`linkedin/tasks/follow_up.py`** — `handle_follow_up`, rate limiting.
- **`linkedin/pipeline/qualify.py`** — `run_qualification()`, `fetch_qualification_candidates()`.
- **`linkedin/pipeline/search.py`** — `run_search()`, keyword management.
- **`linkedin/pipeline/search_keywords.py`** — `generate_search_keywords()` via LLM.
- **`linkedin/pipeline/ready_pool.py`** — GP confidence gate, `promote_to_ready()`.
- **`linkedin/pipeline/pools.py`** — Composable generators: `search_source` → `qualify_source` → `ready_source`.
- **`linkedin/pipeline/freemium_pool.py`** — Seed priority + undiscovered pool, ranked by qualifier.
- **`linkedin/ml/qualifier.py`** — `Qualifier` protocol, `BayesianQualifier`, `KitQualifier`, `qualify_with_llm()`.
- **`linkedin/ml/embeddings.py`** — FastEmbed utilities, `embed_text()`, `embed_texts()`.
- **`linkedin/ml/profile_text.py`** — `build_profile_text()`.
- **`linkedin/ml/hub.py`** — HuggingFace kit loader (`fetch_kit()`).
- **`linkedin/browser/session.py`** — `AccountSession` (a `linkedin_cli.session.LinkedInSession`): linkedin_profile, page, context, browser, playwright. `campaigns` cached_property (list, via Campaign.users M2M). `ensure_browser()` launches/recovers browser via `linkedin.browser.launch.start_browser_session`. `self_profile` cached_property — scrapes via the `linkedin_cli` self-discovery primitive on first access (no DB cache; one extra scrape per daemon restart) and persists the disqualified self-lead via `db.leads.register_self_lead`. Cookie expiry check via `_maybe_refresh_cookies()`. `reauthenticate()` forces fresh login.
- **`linkedin/browser/registry.py`** — `get_or_create_session()`, `get_first_active_profile()`, `resolve_profile()`, `cli_parser()`/`cli_session()` (Django bootstrap for `follow_up.py`'s `__main__`).
- **`linkedin/browser/launch.py`** — `start_browser_session()` + `_save_cookies()`: the daemon's launch/persistence orchestration — launch the stealthed browser (via `linkedin_cli.browser.login.launch_browser`), restore/persist cookies to the Django DB, run the login flow (`linkedin_cli.auth.authenticate`), validate a saved session. The reusable browser/login *mechanics* live in `linkedin_cli`; this is the Django/DB glue.
- **`linkedin/db/leads.py`** — Lead CRUD, `get_leads_for_qualification()`, `disqualify_lead()`, `_cache_urn_from_profile()`, `register_self_lead()` (persists the logged-in member as a disqualified self-lead on top of the `linkedin_cli` self-discovery primitive).
- **`core/db/deals.py`** — Deal/state ops, `set_profile_state()` (also fires `_capture_contact_info()` on the CONNECTED transition), `increment_connect_attempts()`, `create_freemium_deal()`.
- **`linkedin/db/chat.py`** — `sync_conversation()`, `_sync_from_api()`, folds newly-synced messages into `Deal.chat_summary` via `update_chat_summary`.
- **`core/db/summaries.py`** — Single mem0-style LLM boundary. `materialize_profile_summary_if_missing(deal, session)` fires on first follow-up touch (one Voyager re-scrape per `(lead, campaign)` lifetime); `update_chat_summary(deal, new_messages, *, seller_name)` folds newly-synced ChatMessages incrementally via `reconcile_facts`, which routes new facts through mem0's UPDATE prompt to apply ADD/UPDATE/DELETE/NONE events (mirrors `mem0/memory/main.py::Memory._add_to_vector_store` lines 594-700, with vector-store ops replaced by an in-memory dict because `Deal.chat_summary` is a flat list). `_format_messages_for_extraction` filters to incoming messages only, so `chat_summary` holds facts about the lead and a one-sided outgoing burst is a noop. `extract_facts(text, *, seller_name, context)` runs `pydantic_ai.Agent(get_llm_model(), output_type=FactList)` against the vendored `_FACT_EXTRACTION_PROMPT` plus an unconditional identity-binding block (`_build_identity_binding`) telling the LLM that `[Me]` is `seller_name`, so seller-name greetings in `[Lead]` messages don't get misattributed to the lead. `reconcile_facts(existing, new, *, seller_name)` prepends the same binding to mem0's UPDATE prompt with an explicit "DELETE contamination" instruction — previously-stored facts that describe the seller as the lead *should* clean up on the next sync that produces a conflicting fact, though this is best-effort (the upstream mem0 prompt is example-heavy and the cleanup hint is one prepended sentence; dormant deals stay contaminated). `seller_name_from(session)` is the single derivation point — `first_name` from `session.self_profile` with username fallback. mem0's `DEFAULT_UPDATE_MEMORY_PROMPT` and `get_update_memory_messages` live under `openoutreach/core/vendor/mem0/configs/prompts.py` (mirrors upstream path so future syncs are a clean diff; pinned commit recorded in the file header).
- **`core/conf.py`** — Config constants, `CAMPAIGN_CONFIG`. LLM construction lives in `llm.py`. (Browser/Voyager/fixture constants moved to `linkedin_cli/conf.py`.)
- **`core/llm.py`** — `get_llm_model()` factory + `run_agent_sync(coro)` sync boundary. `get_llm_model()` reads `SiteConfig` and dispatches via per-provider builders (OpenAI / Anthropic / Google / Groq / Mistral / Cohere / openai_compatible) to the right `pydantic_ai.models.Model`. Call sites build `Agent(get_llm_model(), ...)` and invoke `run_agent_sync(agent.run(prompt))` — never `Agent.run_sync`, whose anyio portal leaves the caller's thread running-loop slot populated and poisons subsequent sync Playwright calls (`"using Playwright Sync API inside the asyncio loop"`). `run_agent_sync` drives the coroutine to completion on a short-lived worker thread with its own event loop; per-thread asyncio slots are independent, so the caller's thread stays clean regardless of what anyio / pytest-anyio / Jupyter / etc. did to it.
- **`core/onboarding.py`** — Interactive setup.
- **`core/agents/follow_up.py`** — Follow-up agent. Single LLM call with structured output (`FollowUpDecision`). Conversation is read in Python and injected into the prompt. No tool-calling loop.
- **`linkedin/api/newsletter.py`** — `subscribe_to_newsletter()` via Brevo form, `ensure_newsletter_subscription()`. No config parsing — subscribe_newsletter is a BooleanField. (The LinkedIn-platform `api/` — `client`, `voyager`, `messaging/` — moved to `linkedin_cli`.)
- **`linkedin/setup/freemium.py`** — `import_freemium_campaign()`, `seed_profiles()`.
- **`linkedin/setup/gdpr.py`** — `apply_gdpr_newsletter_override()`.
- **`linkedin/setup/seeds.py`** — User-provided seed profiles: parse URLs, create Leads + QUALIFIED Deals.
- **`core/management/setup_crm.py`** — Idempotent CRM bootstrap (Site creation).
- **`admin.py`** (per app) — Django Admin: `core/admin.py` (SiteConfig, Campaign, Task), `linkedin/admin.py` (LinkedInProfile, SearchKeyword, ActionLog), `chat/admin.py` (ChatMessage).
- **`settings.py`** — Django settings (SQLite at `data/db.sqlite3`). Apps: crm, chat, core, linkedin, emails.


## `linkedin_cli` — Standalone LinkedIn Library (Django-free)

External package ([`eracle/linkedin-cli`](https://github.com/eracle/linkedin-cli),
installed via the `requirements/base.txt` git dependency) holding the LinkedIn
*platform mechanics* (browser nav, login form, Voyager API, profile/conversation
scrape, the connect/message/status/thread verbs), so the daemon and external
agents share one surface. Imports with **no Django** configured and holds no DB.
The module docs below describe the installed package's surface.

**Transport — bind + connect.** A session *owner* launches a browser and
`browser.bind()`s it (Playwright ≥1.59); clients attach via `chromium.connect()`
with a real `Page`, and `playwright-cli attach <name>` can share the same browser
(e.g. for a human to clear a checkpoint). The daemon owns its browser in-process;
the standalone CLI's `session open` launcher owns it for non-daemon use.

- **`session.py`** — `LinkedInSession` Protocol (the contract verbs run against:
  `page`, `context`, `self_profile`, `ensure_browser()`, `wait()`, `close()`).
  `PlaywrightCliSession` connects to a bound browser (`chromium.connect(endpoint)`).
  Session registry (`write_session`/`read_session`/`clear_session`,
  `linkedin_cli_home()`) maps a session name → bound-browser websocket endpoint.
- **`launcher.py`** — `open_bound_session()`: launch a persistent browser,
  `bind()` it, register the endpoint, block. The standalone session owner.
- **`cli.py`** — verb CLI over bind+connect (`python -m linkedin_cli.cli`):
  `session open/close`, `login`, `whoami`, `search`, `profile`, `status`, `connect`,
  `message`, `thread`. **Output contract** (documented in the module docstring so
  it travels with the package): every verb produces a result dict; the default is
  a brief human-readable summary on stdout, and `--json` (on every verb) emits the
  full dict — redirect with `>` to save it (no `--out`; clig.dev composability).
  stdout carries only the result; logs and errors go to stderr as
  `error: <type>: <message>` + non-zero exit (`type` mirrors `exceptions.py`).
  Owns interaction-pacing policy (injected into the session).
- **`page_state.py`** — the page-state machine. `classify_page(page)` judges the
  live page by **URL path only** (a `/login?session_redirect=…/feed/` redirect must
  not read as the feed). `@transition(when=, then=)` is a contract decorator over an
  action: it enforces the precondition state *and*, re-reading the page after the
  action, that the result is in the allowed `then` set — raising `IllegalPageTransition`
  otherwise (the postcondition is what a held-state FSM can't express). `PageFlow` is
  the generic engine: `.transition` registers an action under its `when`; `.run()` is
  the single observe→act loop that drives a session to the flow's goal.
- **`auth.py`** — the auth flow, declared as `@auth_flow.transition` actions
  (unknown/authwall/login/checkpoint → feed); no hand-written loop. `authenticate(session,
  *, username=, password=)` stamps credentials and runs the flow to the feed. Shared by
  the CLI `login` verb and the daemon (`openoutreach/linkedin/browser/launch.py`), so both drive one
  enforced login path. Rejected credentials = landing back on `/login`, which the
  `_from_login` contract forbids → surfaces as `AuthenticationError` (and enforces
  never-resubmit).
- **`linkedin/browser/login.py`** — login form mechanics: locators,
  `submit_login_form(session, username, password)` (fills + submits, asserts nothing —
  the auth flow re-reads the page), `dismiss_comply_gate()`, `await_checkpoint_clear()`,
  `launch_browser()`.
- **`linkedin/browser/nav.py`** — `goto_page()`, `human_type()`, `find_top_card()`, `dump_page_html()`.
- **`actions/`** — `connect.py` (`send_connection_request`), `status.py`
  (`get_connection_status`), `message.py` (`send_raw_message`), `profile.py`
  (`scrape_profile`), `search.py` (`search_people` — returns a
  `{query, page, network, profiles:[{public_identifier, url}]}` envelope, optional
  `network` degree filter; backs the `search` verb and is used in-process by the
  daemon — plus `visit_profile`), `conversations.py` (`get_conversation`).
- **`api/client.py`** — `PlaywrightLinkedinAPI`: in-page `fetch()` for authentic
  headers; `get_profile()` with tenacity retry; `VOYAGER_REQUEST_TIMEOUT_MS`.
- **`api/voyager.py`** — `LinkedInProfile` parse (`parse_linkedin_voyager_response()`,
  `parse_connection_degree()`).
- **`api/messaging/`** — `send.py` (`send_message`), `conversations.py`
  (`fetch_conversations`/`fetch_messages`), `utils.py` (`encode_urn`/`check_response`).
- **`linkedin/setup/self_profile.py`** — `discover_self_profile(session)`: Voyager `me`
  scrape → dict, no persistence (the disqualified-lead write is OpenOutreach's
  `db.leads.register_self_lead`).
- **`core/conf.py`** — browser/timeout/fixture constants (`BROWSER_*`, `HUMAN_TYPE_*`,
  `BROWSER_HEADLESS`, `CHECKPOINT_RESOLVE_TIMEOUT_S`, fixture dirs).
  **`exceptions.py`** (`AuthenticationError`, `SkipProfile`,
  `ProfileInaccessibleError`, `ReachedConnectionLimit`, `CheckpointChallengeError`),
  **`enums.py`** (`ProfileState`), **`url_utils.py`** (`url_to_public_id`/`public_id_to_url`).


## Configuration

- **`SiteConfig`** (DB singleton) — `llm_provider` (required, defaults to `openai`; choices: `openai`/`anthropic`/`google`/`groq`/`mistral`/`cohere`/`openai_compatible`), `llm_api_key` (required), `ai_model` (required), `llm_api_base` (required only for `openai_compatible`), `finder_api_key` (optional — BetterContact email-finder key; blank disables enrichment). Editable via Django Admin.
- **`conf.py` schedule** — `ENABLE_ACTIVE_HOURS` (`False`), `ACTIVE_START_HOUR` (9), `ACTIVE_END_HOUR` (19), `ACTIVE_TIMEZONE` (system-local IANA name, falls back to "UTC"). Daemon sleeps outside this window. No weekend/rest-day handling — humans use LinkedIn 7 days a week.
- **`conf.py` planner cap** — `CHECK_PENDING_DAILY_CAP` (100). Maximum `check_pending` slots planned per 24h window per campaign; overflow rolls into the next planning cycle.
- **`conf.py:CAMPAIGN_CONFIG`** — `min_ready_to_connect_prob` (0.9), `min_positive_pool_prob` (0.20), `check_pending_recheck_after_hours` (24), `qualification_n_mc_samples` (100), `enrich_min_delay_seconds` (6), `enrich_max_delay_seconds` (10), `enrich_max_per_page` (10), `burst_min_seconds` (2700), `burst_max_seconds` (3900), `break_min_seconds` (600), `break_max_seconds` (1200), `min_action_interval` (120), `embedding_model` ("BAAI/bge-small-en-v1.5").
- **Prompt templates** (at `openoutreach/core/templates/prompts/`) — `qualify_lead.j2` (temp 0.7), `search_keywords.j2` (temp 0.9), `follow_up_agent.j2`.
- **`requirements/`** — `base.txt`, `local.txt`, `production.txt`, `crm.txt` (empty — DjangoCRM installed via `--no-deps`).

## Docker

Base image: `mcr.microsoft.com/playwright/python:v1.55.0-noble`. VNC on port 5900. `BUILD_ENV` arg selects requirements. Dockerfile at `compose/linkedin/Dockerfile`. Install: uv pip → DjangoCRM `--no-deps` → requirements → Playwright chromium.

## CI/CD

- `tests.yml` — pytest in Docker on push to `master` and PRs.
- `deploy.yml` — Tests → build + push to `ghcr.io/eracle/openoutreach`. Tags: `latest`, `sha-<commit>`, semver.

## Dependencies

`requirements/` files. DjangoCRM's `mysqlclient` excluded via `--no-deps`. `uv pip install` for fast installs.

Core: `playwright`, `playwright-stealth`, `Django`, `django-crm-admin`, `pandas`, `pydantic-ai-slim` (with `openai`/`anthropic`/`google`/`groq`/`mistral`/`cohere`/`bedrock` extras), `jinja2`, `pydantic`, `jsonpath-ng`, `tendo`, `termcolor`, `tenacity`
ML: `scikit-learn`, `numpy`, `fastembed`, `joblib`
