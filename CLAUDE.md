# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OpenOutreach is a self-hosted LinkedIn automation tool for B2B lead generation. It uses Playwright with stealth plugins for browser automation and LinkedIn's internal Voyager API for structured profile data. The CRM backend is powered by DjangoCRM with Django Admin UI.

## Commands

### Local Development
```bash
python -m venv venv && source venv/bin/activate
make setup                           # install deps + migrate + bootstrap CRM
playwright install --with-deps chromium
python main.py load urls.csv         # import profile URLs from CSV into CRM
python main.py run                   # run the daemon (interactive onboarding on first run)
python main.py run <handle>          # run the daemon with a specific account
python main.py run --product-docs docs.txt --campaign-objective obj.txt  # skip interactive onboarding
python main.py generate-keywords docs.md "sell X to Y"  # generate campaign keywords via LLM
python manage_crm.py runserver       # Django Admin at http://localhost:8000/admin/
make analytics                       # build dbt models (DuckDB analytics)
make analytics-test                  # run dbt schema tests
```

### Testing
```bash
pytest                            # run all tests
pytest tests/api/test_voyager.py  # run single test file
pytest -k test_name               # run single test by name
make test                         # run tests via Docker
```

### Docker
```bash
make build    # build containers
make up       # build + run
make stop     # stop services
make attach   # follow logs
make up-view  # run + open VNC viewer
```

## Architecture

### Entry Flow
`main.py` (Django bootstrap + auto-migrate + CRM setup) → argparse subcommands:
- `load <csv>` — imports profile URLs into CRM via `csv_launcher.load_profiles_df()` + `crm_profiles.add_profile_urls()`
- `run [handle]` — runs onboarding (if needed), then launches `daemon.run_daemon()` which time-spreads actions across configurable working hours
- `generate-keywords <product_docs> "<objective>"` — calls LLM to generate `assets/campaign_keywords.yaml` with positive/negative/exploratory keyword lists for ML scoring

### Onboarding (`onboarding.py`)
Before the daemon starts, `ensure_keywords()` ensures campaign keywords exist. Three paths:

1. **CLI file args** (`python main.py run --product-docs docs.txt --campaign-objective obj.txt`) — reads both files, calls LLM to generate keywords, persists all three files to `assets/campaign/`.
2. **Already onboarded** — if `assets/campaign/campaign_keywords.yaml` exists, does nothing.
3. **Interactive onboarding** — if no keywords file exists and no CLI args, prompts the user in the terminal:
   - Product/service description (multi-line, Ctrl-D to finish)
   - Campaign objective (multi-line, Ctrl-D to finish)
   - Validates non-empty, persists inputs to `assets/campaign/product_docs.txt` and `assets/campaign/campaign_objective.txt`, then calls LLM to generate keywords.

Persisted files under `assets/campaign/`: `product_docs.txt`, `campaign_objective.txt`, `campaign_keywords.yaml`.

### Profile State Machine
Each profile progresses through states defined in `navigation/enums.py:ProfileState`:
`DISCOVERED` → `ENRICHED` → `PENDING` → `CONNECTED` → `COMPLETED` (or `FAILED` / `IGNORED`)

States map to DjangoCRM Deal Stages (defined in `db/crm_profiles.py:STATE_TO_STAGE`).

The daemon (`daemon.py`) spreads actions across configurable working hours (default 09:00–18:00, OS local timezone). Three **major lanes** are scheduled at fixed intervals derived from `active_hours / daily_limit` (±20% random jitter). **Enrichment** dynamically fills the gaps between major actions (`gap / profiles_to_enrich`, floored at `enrich_min_interval`). Outside working hours the daemon sleeps until the next window starts.

1. **Check Pending** (scheduled, highest priority) — checks PENDING profiles for acceptance → CONNECTED (triggers ML retrain via dbt). Uses exponential backoff per profile: initial interval = `check_pending_recheck_after_hours`, doubles each time a profile is still pending.
2. **Follow Up** (scheduled) — sends follow-up message to CONNECTED profiles → COMPLETED. Contacts profiles immediately once discovered as connected. Interval = active_minutes / follow_up_daily_limit.
3. **Connect** (scheduled) — ML-ranks ENRICHED profiles, sends connection request → PENDING. Interval = active_minutes / connect_daily_limit.
4. **Enrich** (gap-filling) — scrapes 1 DISCOVERED profile per tick via Voyager API → ENRICHED (or IGNORED if pre-existing connection). Paced to fill time between major actions.

The `IGNORED` state is a terminal state for pre-existing connections (already connected before the automation ran). Controlled by `follow_up_existing_connections` config flag.

### CRM Data Model
- **Lead** — Created per LinkedIn profile URL. Stores `first_name`, `last_name`, `title`, `website` (LinkedIn URL), `description` (full parsed profile JSON).
- **Contact** — Created after enrichment, linked to Company.
- **Company** — Created from first position's company name.
- **Deal** — Tracks pipeline stage. One Deal per Lead. Stage maps to ProfileState. `next_step` field stores JSON metadata (e.g. `{"backoff_hours": N}` for exponential backoff in check_pending).
- **TheFile** — Raw Voyager API JSON attached to Lead via GenericForeignKey.

### Key Modules
- **`daemon.py`** — Main daemon loop. Creates ML scorer, rate limiters, `LaneSchedule` objects for three major lanes, and an enrich lane for gap-filling. Spreads actions across working hours; enrichments dynamically fill gaps between scheduled major actions. Auto-rebuilds analytics (`dbt run`) if scorer encounters a schema mismatch.
- **`lanes/`** — Action lanes executed by the daemon:
  - `enrich.py` — Scrapes 1 DISCOVERED profile per tick via Voyager API. Detects pre-existing connections (IGNORED). Exports `is_preexisting_connection()` shared helper.
  - `connect.py` — ML-ranks ENRICHED profiles, sends connection requests. Catches pre-existing connections missed by enrich (when `connection_degree` was None at scrape time).
  - `check_pending.py` — Checks PENDING profiles for acceptance. Uses exponential backoff: doubles `backoff_hours` in `deal.next_step` each time a profile is still pending. Triggers dbt rebuild + ML retrain when connections flip.
  - `follow_up.py` — Sends follow-up messages to CONNECTED profiles.
- **`ml/keywords.py`** — Keyword loading (`load_keywords`), profile text extraction (`build_profile_text`), boolean keyword presence features (`compute_keyword_features`), and cold-start heuristic scoring (`cold_start_score`). Keywords are loaded from `assets/campaign_keywords.yaml`.
- **`ml/scorer.py:ProfileScorer`** — HistGradientBoostingClassifier + Thompson Sampling for profile ranking. Uses 24 mechanical features + dynamic boolean keyword presence features from campaign keywords. Trains from `analytics.duckdb` mart. Falls back to cold-start keyword heuristic (count of distinct positive keywords present minus negative, if keywords exist) or FIFO (if no keywords). On schema mismatch, the daemon auto-rebuilds analytics via `dbt run`.
- **`rate_limiter.py:RateLimiter`** — Daily/weekly rate limits with auto-reset. Supports external exhaustion (LinkedIn-side limits).
- **`sessions/account.py:AccountSession`** — Central session object holding Playwright browser, Django User, and account config. Passed throughout the codebase.
- **`db/crm_profiles.py`** — Profile CRUD backed by DjangoCRM models. `get_profile()` returns a plain dict with `state` and `profile` keys. Includes `get_enriched_profiles()`, `get_pending_profiles()` (per-profile exponential backoff via `deal.next_step`), `get_connected_profiles()` for lane queries. `_deal_to_profile_dict()` includes a `meta` key with parsed `next_step` JSON. `set_profile_state()` clears `next_step` on any transition to/from PENDING. CRM lookups are `@lru_cache`d.
- **`onboarding.py`** — Interactive onboarding and keyword generation. `ensure_keywords()` handles three paths (CLI files, already onboarded, interactive). `generate_keywords()` calls LLM via Jinja2 prompt template and validates the YAML response. `_read_multiline()` reads multi-line terminal input (Ctrl-D to finish).
- **`conf.py`** — Loads config from `.env` and `assets/accounts.secrets.yaml`. Exports `CAMPAIGN_CONFIG` dict (rate limits, timing, feature flags), `KEYWORDS_FILE` / `PRODUCT_DOCS_FILE` / `CAMPAIGN_OBJECTIVE_FILE` paths. All paths derived from `ASSETS_DIR`.
- **`api/voyager.py`** — Parses LinkedIn's Voyager API JSON responses into clean dicts via internal dataclasses (`LinkedInProfile`, `Position`, `Education`). Uses URN reference resolution from the `included` array.
- **`django_settings.py`** — Django settings importing DjangoCRM's default settings. SQLite DB at `assets/data/crm.db`.
- **`management/setup_crm.py`** — Idempotent bootstrap: creates Department, Users, Deal Stages (including Ignored), ClosingReasons, LeadSource.
- **`templates/renderer.py`** — Jinja2 or AI-prompt-based message rendering. Template type (`jinja` or `ai_prompt`) configured per account. AI calls go through LangChain.
- **`navigation/`** — Login flow, throttling, browser utilities. `utils.py` always extracts `/in/` profile URLs from pages visited (auto-discovery).
- **`actions/`** — Individual browser actions (scrape, connect, message, search).

### Configuration
- **`assets/accounts.secrets.yaml`** — Single config file containing: `env:` (API keys, model), `campaign:` (rate limits, timing, feature flags), `accounts:` (credentials, CSV path, template). Copy from `assets/accounts.secrets.template.yaml`.
- **`campaign:` section** — `connect.daily_limit`, `connect.weekly_limit`, `check_pending.recheck_after_hours` (base interval, doubles per profile via exponential backoff), `follow_up.daily_limit`, `follow_up.existing_connections` (false = ignore pre-existing connections), `enrich_min_interval` (floor seconds between enrichment API calls, default 1), `working_hours.start` / `working_hours.end` (HH:MM, default 09:00–18:00, OS local timezone).
- **`assets/campaign/campaign_keywords.yaml`** — Generated by `generate-keywords` subcommand or interactive onboarding. Contains `positive`, `negative`, and `exploratory` keyword lists used by the ML scorer for boolean presence features and cold-start ranking.
- **`assets/campaign/product_docs.txt`** — Persisted product/service description from onboarding. Used to regenerate keywords if needed.
- **`assets/campaign/campaign_objective.txt`** — Persisted campaign objective from onboarding.
- **`assets/templates/prompts/generate_keywords.j2`** — Jinja2 prompt template for LLM-based keyword generation. Receives `product_docs` and `campaign_objective` variables.
- **`requirements/`** — `crm.txt` (DjangoCRM, installed with `--no-deps`), `base.txt` (runtime deps, includes `analytics.txt`), `analytics.txt` (dbt-core + dbt-duckdb), `local.txt` (adds pytest/factory-boy), `production.txt`.

### Analytics Layer (dbt + DuckDB)
The `analytics/` directory contains a dbt project that reads from the CRM SQLite DB (via DuckDB's SQLite attach) to build ML training sets. No CRM data is modified.

- **`analytics/profiles.yml`** — DuckDB profile config. Attaches `assets/data/crm.db` as `crm`. Memory limit set to 2GB.
- **`analytics/models/staging/`** — Staging views over CRM tables (`stg_leads`, `stg_deals`, `stg_stages`). Lead JSON fields (including `industry_name`, `geo_name`) are parsed here.
- **`analytics/models/marts/ml_connection_accepted.sql`** — Binary classification training set: did a connection request get accepted? Target=1 (reached CONNECTED/COMPLETED), Target=0 (stuck at PENDING). Excludes DISCOVERED/ENRICHED/FAILED profiles. Uses LATERAL UNNEST CTEs to extract 24 mechanical features from positions/educations JSON arrays, plus a concatenated `profile_text` column for keyword feature extraction in Python.
- **Output:** `assets/data/analytics.duckdb` — query with `duckdb.connect("assets/data/analytics.duckdb")`.
- **Deps:** `dbt-core 1.11.x` + `dbt-duckdb 1.10.x` + `protobuf` 6.33.x (pinned; earlier 6.32.x had a memory regression ~5GB RSS on startup, resolved in 6.33+).

### Error Handling Convention
The application should crash on unexpected errors. `try/except` blocks should only handle expected, recoverable errors. Custom exceptions in `navigation/exceptions.py`: `TerminalStateError`, `SkipProfile`, `ReachedConnectionLimit`.

### Dependencies
Core: `playwright`, `playwright-stealth`, `Django`, `django-crm-admin` (installed via `--no-deps` to skip mysqlclient), `pandas`, `langchain`/`langchain-openai`, `jinja2`, `pydantic`, `jsonpath-ng`, `tendo`, `termcolor`
ML: `scikit-learn`, `duckdb`
Analytics: `dbt-core` 1.11.x, `dbt-duckdb` 1.10.x, `protobuf` 6.33.x (6.32.x had memory regression, resolved in 6.33+)
