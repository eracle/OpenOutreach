# System Architecture

This document outlines the architecture of OpenOutreach, from data ingestion and storage to the daemon-driven
workflow engine.

## High-Level Overview

The system automates LinkedIn outreach through a daemon that schedules actions across configurable working hours:

1. **Input**: A seed profile is loaded on startup, and new profiles are auto-discovered as the daemon navigates LinkedIn pages. When the pipeline has nothing left to process, LLM-generated search keywords are used to discover new profiles.
2. **Enrichment**: The daemon scrapes detailed profile data via LinkedIn's internal Voyager API, stores it in the CRM, and computes embeddings.
3. **Qualification**: Profiles are qualified using a Gaussian Process Classifier with BALD active learning — the model selects the most informative profiles to query via LLM, and auto-decides on confident ones.
4. **Outreach**: Connection requests are sent to the highest-ranked qualified profiles, and follow-up messages are sent after acceptance.
5. **State Tracking**: Each profile progresses through a state machine (`DISCOVERED` → `ENRICHED` → `QUALIFIED` → `PENDING` → `CONNECTED` → `COMPLETED`), tracked as Deal stages in the CRM.

## Core Data Model (DjangoCRM)

The system uses DjangoCRM with a single SQLite database at `assets/data/crm.db`. The key models are:

- **Lead** — One per LinkedIn profile URL. Stores `first_name`, `last_name`, `title`, `website` (LinkedIn URL), `description` (full parsed profile JSON).
- **Contact** — Created after enrichment, linked to a Company.
- **Company** — Created from the first position's company name.
- **Deal** — Tracks pipeline stage (maps to `ProfileState`). One Deal per Lead. The `next_step` field stores JSON metadata (e.g. `{"backoff_hours": N}` for exponential backoff).
- **TheFile** — Raw Voyager API JSON attached to a Lead via `GenericForeignKey`.

### Profile State Machine

Defined in `linkedin/navigation/enums.py:ProfileState`:

```
DISCOVERED → ENRICHED → QUALIFIED → PENDING → CONNECTED → COMPLETED
                            ↓                               (or FAILED / IGNORED)
                       DISQUALIFIED
```

States map to DjangoCRM Deal Stages via `db/crm_profiles.py:STATE_TO_STAGE`. The `IGNORED` state is terminal, used for pre-existing connections (controlled by the `follow_up.existing_connections` config flag). The `DISQUALIFIED` state is terminal, for profiles rejected by the qualification pipeline.

## Daemon (`linkedin/daemon.py`)

The daemon is the central orchestrator. It runs within configurable working hours (default 09:00-18:00,
OS local timezone) and sleeps outside the window.

### Scheduling

Three **major lanes** are priority-scheduled (not round-robin). The daemon always picks the lane whose next run
time is soonest:

| Priority | Lane | Interval | Description |
|----------|------|----------|-------------|
| 1 (highest) | **Connect** | `min_action_interval` (default 120s) | ML-ranks and sends connection requests |
| 2 | **Check Pending** | `recheck_after_hours` (default 24h) | Polls PENDING profiles for acceptance |
| 3 | **Follow Up** | `min_action_interval` (default 120s) | Sends messages to CONNECTED profiles |

Each major lane is tracked by a `LaneSchedule` object with a `next_run` timestamp. After execution,
`reschedule()` sets the next run to `time.time() + interval * jitter` (jitter = uniform 0.8-1.2).
Daily and weekly rate limiters independently cap totals (e.g. 20 connects/day, 100/week).

**Enrichment** and **qualification** are gap-filling lanes: between major actions, the daemon fills idle time by
scraping DISCOVERED profiles and qualifying ENRICHED ones. The gap-filler interval is
`gap_to_next_major / total_work`, floored at `enrich_min_interval` (default 1 second).

**Search** is the lowest-priority gap-filler: it only fires when both enrich and qualify have nothing to do.
It uses LLM-generated LinkedIn People search keywords to discover new profiles.

## Lanes (`linkedin/lanes/`)

Each lane is a class with `can_execute()` and `execute()` methods:

### `enrich.py` — EnrichLane
- Scrapes 1 DISCOVERED profile per tick via the Voyager API.
- Detects pre-existing connections (`connection_degree == 1`) and marks them IGNORED.
- Exports `is_preexisting_connection()` as a shared helper used by the connect lane.

### `qualify.py` — QualifyLane
- Two-phase qualification lane:
  1. Embeds ENRICHED profiles that lack embeddings (backfill).
  2. Qualifies embedded profiles via GPC active learning — BALD selects the most informative candidate, predictive entropy gates auto-decisions (low entropy → auto-accept/reject, high entropy or model unfitted → LLM query via `qualify_lead.j2` prompt).
- Transitions profiles to QUALIFIED or DISQUALIFIED.

### `connect.py` — ConnectLane
- ML-ranks all QUALIFIED profiles using `BayesianQualifier.rank_profiles()` (by GPC predictive probability).
- Sends a connection request to the top-ranked profile.
- Catches pre-existing connections missed during enrichment (when `connection_degree` was None at scrape time) via UI-based detection.
- Respects daily and weekly rate limits via `RateLimiter`.

### `check_pending.py` — CheckPendingLane
- Checks PENDING profiles for acceptance via browser UI inspection.
- Uses exponential backoff per profile: initial = `recheck_after_hours` (default 24h), doubles each time via `deal.next_step` JSON metadata.

### `follow_up.py` — FollowUpLane
- Sends a follow-up message to the first CONNECTED profile.
- Uses the account's configured template (Jinja2 or AI-prompt).
- Transitions profile to COMPLETED on success.
- Respects daily rate limit via `RateLimiter`.

### `search.py` — SearchLane
- Lowest-priority gap-filler — only fires when enrich and qualify both have nothing to do.
- Uses `generate_search_keywords()` from `ml/search_keywords.py` to get LLM-generated LinkedIn People search keywords from campaign context.
- Iterates through keywords, popping one per `execute()` call. Refills from the LLM when exhausted.
- Discovered profile URLs are captured by the auto-discovery mechanism in `navigation/utils.py`.

## API Client (`linkedin/api/`)

- **`client.py`** — `PlaywrightLinkedinAPI` class. Uses the browser's active Playwright context to make
  authenticated GET requests to LinkedIn's Voyager API. Automatically extracts `csrf-token` and session headers.
- **`voyager.py`** — Parses Voyager API JSON responses into clean `LinkedInProfile` dataclasses (with `Position`,
  `Education` sub-objects). Resolves URN references from the `included` array.
- **`emails.py`** — Newsletter subscription utility (`ensure_newsletter_subscription`).

## Navigation (`linkedin/navigation/`)

Handles browser automation and state management:

- **`login.py`** — Automates login, handles MFA, manages cookie persistence for session reuse across runs.
- **`utils.py`** — Browser helpers including `human_delay` for realistic pauses and automatic URL discovery
  (extracts `/in/` profile URLs from every page visited, filtering out `/in/me/` and the account's own handle).
- **`exceptions.py`** — Custom exceptions:
  - `AuthenticationError` — 401 / login failure
  - `TerminalStateError` — profile is in a terminal state, must be skipped
  - `SkipProfile` — profile should be skipped for other reasons
  - `ReachedConnectionLimit` — weekly connection limit hit
- **`enums.py`** — `ProfileState` (9 states: DISCOVERED, ENRICHED, QUALIFIED, PENDING, CONNECTED, COMPLETED, FAILED, IGNORED, DISQUALIFIED) and `MessageStatus` (`SENT`, `SKIPPED`).

## Actions (`linkedin/actions/`)

Low-level, reusable browser actions composed by the lanes:

- **`connect.py`** — `send_connection_request()`: navigates to profile, checks status, sends invite. Returns `ProfileState.PENDING` on success. Raises `ReachedConnectionLimit` if LinkedIn blocks.
- **`connection_status.py`** — `get_connection_status()`: determines relationship via Voyager API degree + UI fallback (inspects buttons/badges).
- **`message.py`** — `send_follow_up_message()`: renders message from template, sends via popup or direct messaging window. Includes clipboard fallback if typing fails.
- **`profile.py`** — Profile page navigation utilities.
- **`search.py`** — `search_profile()`: navigates to a profile page. `search_people()`: executes LinkedIn People search by keyword.

## ML Qualification (`linkedin/ml/`)

### `qualifier.py` — BayesianQualifier

- **Model**: `GaussianProcessClassifier` (scikit-learn, `ConstantKernel * RBF`) with BALD active learning.
- **Input**: 384-dimensional FastEmbed embeddings (BAAI/bge-small-en-v1.5 by default).
- **Lazy refit**: `update(embedding, label)` appends training data and invalidates the fit. `_ensure_fitted()` re-fits on ALL accumulated data (O(n³)) when predictions are needed. Previously-fitted kernel params seed the optimizer for fast refits.
- **`predict(embedding)`** — Returns `(prob, entropy)` or `None` if unfitted (cold start / single class).
- **`bald_scores(embeddings)`** — Computes BALD via MC sampling from the GP latent posterior (`f_mean`, `f_var` from sklearn internals). Returns array or `None` if unfitted.
- **`rank_profiles(profiles)`** — Sorts QUALIFIED profiles by predicted probability (descending).
- **`warm_start(X, y)`** — Bulk-loads historical labels and fits once (used on daemon restart).
- **Cold start**: GPC needs both positive and negative labels to fit. Until then, `predict`/`bald_scores` return `None` and the qualify lane defers to the LLM.

### `embeddings.py` — DuckDB Vector Store

- Uses `fastembed` for embedding generation (model configurable, default BAAI/bge-small-en-v1.5).
- Functions: `embed_text()`, `embed_texts()`, `embed_profile()`, `store_embedding()`, `store_label()`, `get_all_unlabeled_embeddings()`, `get_unlabeled_profiles()`, `get_labeled_data()`, `count_labeled()`, `get_embedded_lead_ids()`, `ensure_embeddings_table()`.

### `profile_text.py`

- `build_profile_text()` — Concatenates all text fields from a profile dict (headline, summary, positions, educations, etc.), lowercased. Used as input for embedding generation.

### `search_keywords.py`

- `generate_search_keywords()` — Calls LLM via `search_keywords.j2` prompt template with product docs and campaign objective to generate LinkedIn People search queries. Returns a list of search query strings.

## Analytics Layer (`analytics/`)

A dbt project that builds ML training sets from the CRM data:

- **Engine**: DuckDB, attaches the CRM SQLite DB read-only.
- **Staging models**: `stg_leads`, `stg_deals`, `stg_stages` — parse raw CRM tables.
- **Mart**: `ml_connection_accepted` — binary classification training set (accepted=1 for CONNECTED/COMPLETED, accepted=0 for stuck at PENDING). Outputs 24 mechanical features + `profile_text` for keyword extraction.
- **Output**: `assets/data/analytics.duckdb`.

## Templates (`linkedin/templates/renderer.py`)

Two template types for follow-up messages:

- **`jinja`** — Jinja2 template with access to the `profile` object.
- **`ai_prompt`** — Jinja2 renders a prompt, which is then sent to the configured LLM (via LangChain) to generate the final message.

If a `booking_link` is configured for the account, it is appended to the rendered message.

## Sessions (`linkedin/sessions/account.py`)

`AccountSession` is the central session object passed throughout the codebase. It holds:

- `handle` — account identifier (lowercased)
- `account_cfg` — configuration dict from `conf.py`
- `django_user` — Django User object (auto-created if missing)
- `page`, `context`, `browser`, `playwright` — Playwright browser objects (lazily initialized via `ensure_browser()`)

## Rate Limiting (`linkedin/rate_limiter.py`)

`RateLimiter` enforces daily and weekly action limits with automatic reset:

- `can_execute()` — checks if limits allow another action.
- `record()` — increments counters after an action.
- `mark_daily_exhausted()` — externally signals that LinkedIn itself has blocked further actions for the day.

## CRM Bootstrap (`linkedin/management/setup_crm.py`)

`setup_crm()` is an idempotent bootstrap that creates:

- Department ("LinkedIn Outreach")
- Django Users (one per active account)
- 9 Deal Stages (Discovered, Enriched, Qualified, Disqualified, Pending, Connected, Completed, Failed, Ignored)
- 4 Closing Reasons (Completed, Failed, Ignored, Disqualified)
- LeadSource ("LinkedIn Scraper")
- Default Site (localhost)
- "co-workers" Group (required by DjangoCRM)

## Error Handling Convention

The application crashes on unexpected errors. `try/except` blocks are only used for expected, recoverable errors.
