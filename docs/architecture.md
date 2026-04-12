# System Architecture

This document outlines the architecture of OpenOutreach, from data ingestion and storage to the daemon-driven
workflow engine.

## High-Level Overview

The system automates LinkedIn outreach through a daemon that schedules actions continuously:

1. **Input**: New profiles are auto-discovered as the daemon navigates LinkedIn pages. When the candidate pool runs dry, LLM-generated search keywords are used to discover new profiles.
2. **Enrichment**: The daemon scrapes detailed profile data via LinkedIn's internal Voyager API, stores it in the CRM, and computes embeddings.
3. **Qualification**: Profiles are qualified using a Gaussian Process Regressor with BALD active learning — the model selects the most informative profiles to query via LLM. All decisions go through the LLM; the GP is used only for candidate selection and the confidence gate.
4. **Outreach**: Connection requests are sent to the highest-ranked qualified profiles, and agentic follow-up conversations run after acceptance.
5. **State Tracking**: Each profile progresses through a state machine (implicit discovery/enrichment → `QUALIFIED` → `READY_TO_CONNECT` → `PENDING` → `CONNECTED` → `COMPLETED`), tracked as Deal states in the CRM.

## Core Data Model

The system uses Django with a single SQLite database at `db.sqlite3` (project root). The key models are:

- **Lead** (`crm/models/lead.py`) — One per LinkedIn profile URL. Stores `first_name`, `last_name`, `company_name`, `linkedin_url` (LinkedIn URL, unique), `description` (full parsed profile JSON), `embedding` (BinaryField storing 384-dim fastembed vector as bytes, with `embedding_array` numpy property accessor). `disqualified` (bool) marks permanent account-level exclusion (self-profile, unreachable profiles). `creation_date`, `update_date`.
- **Deal** (`crm/models/deal.py`) — Tracks pipeline state. One Deal per Lead per campaign (campaign-scoped via FK). `state` = CharField (ProfileState choices). `closing_reason` = CharField (ClosingReason: COMPLETED/FAILED/DISQUALIFIED). `reason` = qualification/failure reason. `connect_attempts` = retry count. `backoff_hours` = check_pending backoff. `creation_date`, `update_date`.
- **Campaign** (`linkedin/models.py`) — `name` (unique), `users` (M2M to User for membership), `product_docs`, `campaign_objective`, `booking_link`, `is_freemium` (bool), `action_fraction` (float), `seed_public_ids` (JSONField).
- **LinkedInProfile** (`linkedin/models.py`) — 1:1 with `auth.User`. Stores credentials, rate limits, newsletter preference. Rate-limiting methods: `can_execute()`, `record_action()`, `mark_exhausted()`.
- **SearchKeyword** (`linkedin/models.py`) — FK to Campaign. Stores `keyword`, `used` (bool), `used_at`.
- **ActionLog** (`linkedin/models.py`) — FK to LinkedInProfile + Campaign. Tracks `connect` and `follow_up` actions for rate limiting.
- **Task** (`linkedin/models.py`) — Persistent priority queue for daemon actions. `task_type`, `status`, `scheduled_at`, `payload` (JSONField).
- **ChatMessage** (`chat/models.py`) — GenericForeignKey to any object. `content`, `owner`, `answer_to` (threading), `topic`.

### Profile State Machine

Defined in `linkedin/enums.py:ProfileState`:

```
(url_only) → (enriched) → QUALIFIED → READY_TO_CONNECT → PENDING → CONNECTED → COMPLETED
  (implicit)   (implicit)   (Deal)     (GP confidence gate)  (sent)   (accepted)   (followed up)
                                ↓
                          FAILED (LLM rejection creates campaign-scoped FAILED Deal)
```

Pre-Deal states are implicit: a Lead with no description is "url_only", a Lead with description is "enriched". `ProfileState` is a `models.TextChoices` enum with 6 values: `QUALIFIED`, `READY_TO_CONNECT`, `PENDING`, `CONNECTED`, `COMPLETED`, `FAILED`. Values ARE the CRM stage names (e.g. `ProfileState.QUALIFIED.value == "Qualified"`).

## Daemon (`linkedin/daemon.py`)

The daemon is the central orchestrator. It runs continuously using a **persistent task queue** backed by the `Task` Django model.

### Task Queue Architecture

Tasks are ordered by `scheduled_at` timestamp. The worker loop pops the oldest due task, executes it, and each task handler self-schedules follow-on tasks. On startup, `heal_tasks()` reconciles the queue with CRM state (recovers stale running tasks, seeds missing tasks).

Three task types (all handler functions in `linkedin/tasks/`, signature: `handle_*(task, session, qualifiers)`):

| Task Type | Handler | Scope | Description |
|-----------|---------|-------|-------------|
| `connect` | `handle_connect` | per-campaign | ML-ranks and sends connection requests |
| `check_pending` | `handle_check_pending` | per-profile | Checks one PENDING profile for acceptance |
| `follow_up` | `handle_follow_up` | per-profile | Runs agentic follow-up conversation |

Daily and weekly rate limiters independently cap totals via `LinkedInProfile` methods (DB-backed via `ActionLog`).

Freemium campaigns use the same `connect` task type; the `ConnectStrategy` dataclass (built by `strategy_for()`) handles differences (candidate sourcing, delay, pre-connect hooks) based on `campaign.is_freemium`.

## Task Handlers (`linkedin/tasks/`)

### `connect.py` — handle_connect
- Unified handler for all campaigns via `ConnectStrategy` dataclass.
- Regular campaigns: `find_candidate()` from `pipeline/pools.py` (composable generators: `ready_source` → `qualify_source` → `search_source`).
- Freemium campaigns: `find_freemium_candidate()` from `pipeline/freemium_pool.py` with just-in-time Deal creation.
- Self-reschedules via `strategy.compute_delay(elapsed)`.
- Rate-limited by `LinkedInProfile.can_execute()` / `record_action()`.
- Enqueue helpers: `enqueue_connect()`, `enqueue_check_pending()`, `enqueue_follow_up()` — all backed by `_enqueue_task()` generic dedup helper.

### `check_pending.py` — handle_check_pending
- Checks one PENDING profile via `get_connection_status()`.
- Uses exponential backoff with multiplicative jitter per profile, stored in `deal.backoff_hours`.
- On acceptance → enqueues `follow_up` task.

### `follow_up.py` — handle_follow_up
- Runs the agentic follow-up via `run_follow_up_agent()` from `agents/follow_up.py`. Full docs: [`docs/follow_up_agent.md`](docs/follow_up_agent.md).
- Agent returns a `FollowUpDecision` (structured output: `send_message`/`mark_completed`/`wait`). Handler executes it deterministically.
- `send_message`: sends via `send_raw_message()` (popup → direct thread → Voyager API fallback chain), records ActionLog, re-enqueues.
- `mark_completed`: sets Deal state to COMPLETED with reason.
- `wait`: re-enqueues without sending. Default re-check: 72h.
- On send failure: reverts Deal to QUALIFIED for re-connection.

## Pipeline (`linkedin/pipeline/`)

Candidate sourcing, qualification, and pool management:

- **`qualify.py`** — `run_qualification()`: selects candidates via `qualifier.acquisition_scores()`, always queries LLM for decisions. `fetch_qualification_candidates()` returns `Lead` rows with embeddings for leads awaiting qualification.
- **`search.py`** — `run_search()`: picks next unused keyword (generating fresh ones via LLM if exhausted), runs LinkedIn People search.
- **`search_keywords.py`** — `generate_search_keywords()`: calls LLM to generate LinkedIn People search queries from campaign context.
- **`ready_pool.py`** — GP confidence gate between QUALIFIED and READY_TO_CONNECT. `promote_to_ready()` promotes profiles above `min_ready_to_connect_prob` threshold.
- **`pools.py`** — Composable generators for regular campaigns. `find_candidate()` → `ready_source()` → `qualify_source()` → `search_source()`.
- **`freemium_pool.py`** — `find_freemium_candidate()`: queries `Lead` for embedded leads without a Deal in the campaign.

## API Client (`linkedin/api/`)

- **`client.py`** — `PlaywrightLinkedinAPI` class. Uses in-page `fetch()` to make authenticated requests to LinkedIn's Voyager API.
- **`voyager.py`** — Parses Voyager API JSON responses into clean dicts via internal dataclasses (`LinkedInProfile`, `Position`, `Education`). Resolves URN references from the `included` array.
- **`messaging/`** — Voyager Messaging API package. `send.py`: `send_message()` via REST API. `conversations.py`: `fetch_conversations()` and `fetch_messages()` via Voyager GraphQL. `utils.py`: shared helpers.
- **`newsletter.py`** — Newsletter subscription utilities.

## Browser (`linkedin/browser/`)

Handles browser automation and session management:

- **`session.py`** — `AccountSession`: central session object. Loads `LinkedInProfile` from DB, exposes `linkedin_profile`, `campaign`, `campaigns` (via Campaign.users M2M), `django_user`, and Playwright browser objects (`page`, `context`, `browser`, `playwright`). Key methods: `ensure_browser()`, `wait()`, `_maybe_refresh_cookies()`, `close()`. Credentials are accessed via `linkedin_profile` directly (no config dict).
- **`registry.py`** — `get_or_create_session()`, `get_first_active_profile()`, `resolve_profile()`, `cli_parser()`/`cli_session()` (shared CLI bootstrap for `__main__` scripts).
- **`login.py`** — `launch_browser()`, `start_browser_session()`, `playwright_login()` with human-like typing.
- **`nav.py`** — `goto_page()` (pure navigation), `extract_in_urls()`, `human_type()`, `find_top_card()`, `find_first_visible()`.

## Actions (`linkedin/actions/`)

Low-level, reusable browser actions composed by the task handlers:

- **`connect.py`** — `send_connection_request()`: tries direct button, falls back to More menu. Sends WITHOUT a note. Returns `ProfileState.PENDING` on success, `ProfileState.QUALIFIED` when no Connect button found. Raises `ReachedConnectionLimit` on limit popup.
- **`status.py`** — `get_connection_status()`: fast path via `connection_degree == 1`, fallback to UI text/button inspection.
- **`message.py`** — `send_raw_message()`: sends an arbitrary message via popup or direct messaging thread. Persists via `save_chat_message()`.
- **`conversations.py`** — `get_conversation()`: retrieves past messages with a LinkedIn profile via API scan with navigation fallback.
- **`profile.py`** — `scrape_profile()`: calls Voyager API.
- **`search.py`** — `visit_profile()`: navigates to profile + discovers/enriches nearby `/in/` URLs. `search_people()`: LinkedIn People search with pagination + discovery.

## Database Operations (`linkedin/db/`)

Profile CRUD backed by Django models:

- **`urls.py`** — `url_to_public_id()`, `public_id_to_url()`.
- **`leads.py`** — Lead CRUD: `lead_exists()`, `create_enriched_lead()`, `promote_lead_to_deal()`, `get_leads_for_qualification()`, `disqualify_lead()`, `lead_profile_by_id()`.
- **`deals.py`** — Deal/state operations: `set_profile_state()`, `get_qualified_profiles()`, `get_ready_to_connect_profiles()`, `get_profile_dict_for_public_id()`, `increment_connect_attempts()`, `create_disqualified_deal()`, `create_freemium_deal()`.
- **`enrichment.py`** — Lazy enrichment/embedding: `ensure_lead_enriched()`, `ensure_profile_embedded()`, `load_embedding()`.
- **`chat.py`** — `sync_conversation()`: fetches messages from Voyager API, upserts `ChatMessage` rows by `linkedin_urn`, folds new messages into `Deal.chat_summary` via `update_chat_summary()`. `save_chat_message()` for manual inserts.
- **`summaries.py`** — Lazy mem0-style fact summaries. `materialize_profile_summary_if_missing()`: one-time profile fact extraction. `update_chat_summary()`: incremental chat fact extraction + `reconcile_facts()` (ADD/UPDATE/DELETE/NONE events). See [`docs/follow_up_agent.md`](docs/follow_up_agent.md) for details.

## Agents (`linkedin/agents/`)

- **`follow_up.py`** — Follow-up agent. Single LLM call with structured output (`FollowUpDecision`: `send_message`/`mark_completed`/`wait`). Conversation is synced and injected into the prompt (profile/chat fact summaries + last 6 verbatim messages); no tool-calling loop. System prompt from `follow_up_agent.j2`. Full docs: [`docs/follow_up_agent.md`](docs/follow_up_agent.md).

## ML Qualification (`linkedin/ml/`)

### `qualifier.py` — BayesianQualifier

- **Model**: `GaussianProcessRegressor` (scikit-learn, `ConstantKernel(1.0) * RBF(length_scale=sqrt(384))`) with BALD active learning. Wrapped in `Pipeline(StandardScaler, GPR)`.
- **Input**: 384-dimensional FastEmbed embeddings (BAAI/bge-small-en-v1.5 by default).
- **Lazy refit**: `update(embedding, label)` appends training data and invalidates the fit. `_fit_if_needed()` re-fits on ALL accumulated data (O(n^3)) when predictions are needed.
- **`predict(embedding)`** — Returns `(prob, entropy, std)` or `None` if unfitted (cold start / single class).
- **`predict_probs(embeddings)`** — Returns P(f > 0.5) array (used by confidence gate and acquisition).
- **`compute_bald(embeddings)`** — Computes BALD via MC sampling from the GP posterior.
- **`acquisition_scores(embeddings)`** — Balance-driven strategy: exploit (highest prob) when negatives dominate, explore (highest BALD) otherwise.
- **`rank_profiles(profiles, session)`** — Sorts by raw GP mean (descending).
- **`warm_start(X, y)`** — Bulk-loads historical labels and fits once (used on daemon restart).
- **Cold start**: GPR needs both positive and negative labels to fit. Until then, `predict`/`compute_bald` return `None`.

### `qualifier.py` — KitQualifier

- Standalone qualifier for freemium campaigns. Wraps a pre-trained sklearn-compatible model as a black-box scorer. No inner BayesianQualifier.
- `rank_profiles(profiles, session)` sorts by raw score (descending).

### `embeddings.py`

- Uses `fastembed` for embedding generation (model configurable, default BAAI/bge-small-en-v1.5).
- Functions: `embed_text()`, `embed_texts()`. Embedding storage is handled by `Lead.get_embedding()`.
- Storage and querying handled by the `Lead` model's `embedding` field (with `embedding_array` numpy property accessor).

### `profile_text.py`

- `build_profile_text()` — Concatenates all text fields from a profile dict (headline, summary, positions, educations, etc.), lowercased. Used as input for embedding generation.

### `hub.py`

- `fetch_kit()` — Downloads freemium campaign kit from HuggingFace (`eracle/campaign-kit`), loads `config.json` + `model.joblib`. Cached after first attempt.

## Exceptions (`linkedin/exceptions.py`)

Custom exceptions:
- `AuthenticationError` — 401 / login failure
- `TerminalStateError` — profile is in a terminal state, must be skipped
- `SkipProfile` — profile should be skipped for other reasons
- `ReachedConnectionLimit` — weekly connection limit hit

## CRM Bootstrap (`linkedin/management/setup_crm.py`)

`setup_crm()` is an idempotent bootstrap that creates the default Site (localhost).

## Error Handling Convention

The application crashes on unexpected errors. `try/except` blocks are only used for expected, recoverable errors.
