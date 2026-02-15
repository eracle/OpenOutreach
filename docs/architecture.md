# System Architecture

This document outlines the architecture of OpenOutreach, from data ingestion and storage to the daemon-driven
workflow engine.

## High-Level Overview

The system automates LinkedIn outreach through a daemon that schedules actions across configurable working hours:

1. **Input**: A seed profile is loaded on startup, and new profiles are auto-discovered as the daemon navigates LinkedIn pages.
2. **Enrichment**: The daemon scrapes detailed profile data via LinkedIn's internal Voyager API and stores it in the CRM.
3. **ML Ranking**: Profiles are scored using a gradient boosted trees model (+ Thompson Sampling) trained on historical connection acceptance data.
4. **Outreach**: Connection requests are sent to the highest-ranked profiles, and follow-up messages are sent after acceptance.
5. **State Tracking**: Each profile progresses through a state machine (`DISCOVERED` → `ENRICHED` → `PENDING` → `CONNECTED` → `COMPLETED`), tracked as Deal stages in the CRM.

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
DISCOVERED → ENRICHED → PENDING → CONNECTED → COMPLETED
                                                (or FAILED / IGNORED)
```

States map to DjangoCRM Deal Stages via `db/crm_profiles.py:STATE_TO_STAGE`. The `IGNORED` state is terminal, used for pre-existing connections (controlled by the `follow_up.existing_connections` config flag).

## Daemon (`linkedin/daemon.py`)

The daemon is the central orchestrator. It spreads actions across configurable working hours (default 09:00-18:00,
OS local timezone) and sleeps outside the window.

### Scheduling

Three **major lanes** are priority-scheduled (not round-robin). The daemon always picks the lane whose next run
time is soonest:

| Priority | Lane | Interval | Description |
|----------|------|----------|-------------|
| 1 (highest) | **Connect** | `remaining_minutes / connect_daily_limit` | ML-ranks and sends connection requests |
| 2 | **Check Pending** | `recheck_after_hours` (default 24h) | Polls PENDING profiles for acceptance |
| 3 | **Follow Up** | `remaining_minutes / follow_up_daily_limit` | Sends messages to CONNECTED profiles |

Each major lane is tracked by a `LaneSchedule` object with a `next_run` timestamp. After execution,
`reschedule()` sets the next run to `time.time() + interval * jitter` (jitter = uniform 0.8-1.2).

**Enrichment** is a gap-filling lane: between major actions, the daemon fills idle time by scraping DISCOVERED
profiles. The enrichment interval is `gap_to_next_major / profiles_to_enrich`, floored at `enrich_min_interval`
(default 1 second).

### Analytics Auto-Rebuild

If the ML scorer encounters a schema mismatch (`BinderException`), the daemon automatically runs `dbt run` to
rebuild the analytics DB, then retries training.

## Lanes (`linkedin/lanes/`)

Each lane is a class with `can_execute()` and `execute()` methods:

### `enrich.py` — EnrichLane
- Scrapes 1 DISCOVERED profile per tick via the Voyager API.
- Detects pre-existing connections (`connection_degree == 1`) and marks them IGNORED.
- Exports `is_preexisting_connection()` as a shared helper used by the connect lane.

### `connect.py` — ConnectLane
- ML-ranks all ENRICHED profiles using `ProfileScorer.score_profiles()`.
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
- **`enums.py`** — `ProfileState` (7 states) and `MessageStatus` (`SENT`, `SKIPPED`).

## Actions (`linkedin/actions/`)

Low-level, reusable browser actions composed by the lanes:

- **`connect.py`** — `send_connection_request()`: navigates to profile, checks status, sends invite. Returns `ProfileState.PENDING` on success. Raises `ReachedConnectionLimit` if LinkedIn blocks.
- **`connection_status.py`** — `get_connection_status()`: determines relationship via Voyager API degree + UI fallback (inspects buttons/badges).
- **`message.py`** — `send_follow_up_message()`: renders message from template, sends via popup or direct messaging window. Includes clipboard fallback if typing fails.
- **`profile.py`** — Profile page navigation utilities.
- **`search.py`** — `search_profile()`: navigates to a profile page.

## ML Scoring (`linkedin/ml/`)

### `scorer.py` — ProfileScorer

- **Model**: `HistGradientBoostingClassifier` (scikit-learn) with Thompson Sampling for explore/exploit balance.
- **Features**: 24 mechanical features (profile structure, tenure, education) + dynamic boolean keyword presence features from campaign keywords.
- **Training data**: Reads from `assets/data/analytics.duckdb` mart `ml_connection_accepted`.
- **Fallbacks**: Cold-start keyword heuristic (positive keyword count - negative keyword count) if keywords exist but no training data. FIFO ordering if neither exists.

### `keywords.py`

- `load_keywords()` — loads `campaign_keywords.yaml` (positive, negative, exploratory lists, all lowercased).
- `build_profile_text()` — concatenates all text fields from a profile dict (lowercased).
- `compute_keyword_features()` — boolean 0/1 presence per keyword.
- `cold_start_score()` — count of distinct positive keywords present minus negative (each contributes at most +1/-1).

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
- 7 Deal Stages (Discovered, Enriched, Pending, Connected, Completed, Failed, Ignored)
- 3 Closing Reasons (Completed, Failed, Ignored)
- LeadSource ("LinkedIn Scraper")
- Default Site (localhost)
- "co-workers" Group (required by DjangoCRM)

## Error Handling Convention

The application crashes on unexpected errors. `try/except` blocks are only used for expected, recoverable errors.
