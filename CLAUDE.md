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
python main.py run                   # run the daemon (first active account)
python main.py run <handle>          # run the daemon with a specific account
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
- `run [handle]` — launches `daemon.run_daemon()` which round-robins through four action lanes
- `generate-keywords <product_docs> "<objective>"` — calls LLM to generate `assets/campaign_keywords.yaml` with positive/negative/exploratory keyword lists for ML scoring

### Profile State Machine
Each profile progresses through states defined in `navigation/enums.py:ProfileState`:
`DISCOVERED` → `ENRICHED` → `PENDING` → `CONNECTED` → `COMPLETED` (or `FAILED` / `IGNORED`)

States map to DjangoCRM Deal Stages (defined in `db/crm_profiles.py:STATE_TO_STAGE`).

The daemon (`daemon.py`) round-robins through four lanes that advance profiles through the state machine:
1. **Enrich** — scrapes DISCOVERED profiles via Voyager API → ENRICHED (or IGNORED if pre-existing connection)
2. **Connect** — ML-ranks ENRICHED profiles, sends connection request → PENDING
3. **Check Pending** — checks PENDING profiles for acceptance → CONNECTED (triggers ML retrain via dbt)
4. **Follow Up** — sends follow-up message to CONNECTED profiles → COMPLETED

The `IGNORED` state is a terminal state for pre-existing connections (already connected before the automation ran). Controlled by `follow_up_existing_connections` config flag.

### CRM Data Model
- **Lead** — Created per LinkedIn profile URL. Stores `first_name`, `last_name`, `title`, `website` (LinkedIn URL), `description` (full parsed profile JSON).
- **Contact** — Created after enrichment, linked to Company.
- **Company** — Created from first position's company name.
- **Deal** — Tracks pipeline stage. One Deal per Lead. Stage maps to ProfileState.
- **TheFile** — Raw Voyager API JSON attached to Lead via GenericForeignKey.

### Key Modules
- **`daemon.py`** — Main daemon loop. Creates ML scorer, rate limiters, and four lanes. Round-robins through lanes; sleeps when all idle. Auto-rebuilds analytics (`dbt run`) if scorer encounters a schema mismatch.
- **`lanes/`** — Action lanes executed by the daemon:
  - `enrich.py` — Batch-scrapes DISCOVERED profiles via Voyager API. Detects pre-existing connections (IGNORED). Exports `is_preexisting_connection()` shared helper.
  - `connect.py` — ML-ranks ENRICHED profiles, sends connection requests. Catches pre-existing connections missed by enrich (when `connection_degree` was None at scrape time).
  - `check_pending.py` — Checks PENDING profiles for acceptance. Triggers dbt rebuild + ML retrain when connections flip.
  - `follow_up.py` — Sends follow-up messages to CONNECTED profiles.
- **`ml/keywords.py`** — Keyword loading (`load_keywords`), profile text extraction (`build_profile_text`), keyword count features (`compute_keyword_features`), and cold-start heuristic scoring (`cold_start_score`). Keywords are loaded from `assets/campaign_keywords.yaml`.
- **`ml/scorer.py:ProfileScorer`** — ElasticNet (SGDClassifier) + Thompson Sampling for profile ranking. Uses 24 mechanical features + dynamic keyword count features from campaign keywords. Trains from `analytics.duckdb` mart. Falls back to cold-start keyword heuristic (if keywords exist) or FIFO (if no keywords). On schema mismatch, the daemon auto-rebuilds analytics via `dbt run`.
- **`rate_limiter.py:RateLimiter`** — Daily/weekly rate limits with auto-reset. Supports external exhaustion (LinkedIn-side limits).
- **`sessions/account.py:AccountSession`** — Central session object holding Playwright browser, Django User, and account config. Passed throughout the codebase.
- **`db/crm_profiles.py`** — Profile CRUD backed by DjangoCRM models. `get_profile()` returns a plain dict with `state` and `profile` keys. Includes `get_enriched_profiles()`, `get_pending_profiles()`, `get_connected_profiles()` for lane queries. CRM lookups are `@lru_cache`d.
- **`conf.py`** — Loads config from `.env` and `assets/accounts.secrets.yaml`. Exports `CAMPAIGN_CONFIG` dict (rate limits, timing, feature flags), `KEYWORDS_FILE` path. All paths derived from `ASSETS_DIR`.
- **`api/voyager.py`** — Parses LinkedIn's Voyager API JSON responses into clean dicts via internal dataclasses (`LinkedInProfile`, `Position`, `Education`). Uses URN reference resolution from the `included` array.
- **`django_settings.py`** — Django settings importing DjangoCRM's default settings. SQLite DB at `assets/data/crm.db`.
- **`management/setup_crm.py`** — Idempotent bootstrap: creates Department, Users, Deal Stages (including Ignored), ClosingReasons, LeadSource.
- **`templates/renderer.py`** — Jinja2 or AI-prompt-based message rendering. Template type (`jinja` or `ai_prompt`) configured per account. AI calls go through LangChain.
- **`navigation/`** — Login flow, throttling, browser utilities. `utils.py` always extracts `/in/` profile URLs from pages visited (auto-discovery).
- **`actions/`** — Individual browser actions (scrape, connect, message, search).

### Configuration
- **`assets/accounts.secrets.yaml`** — Single config file containing: `env:` (API keys, model), `campaign:` (rate limits, timing, feature flags), `accounts:` (credentials, CSV path, template). Copy from `assets/accounts.secrets.template.yaml`.
- **`campaign:` section** — `connect.daily_limit`, `connect.weekly_limit`, `check_pending.min_age_days`, `follow_up.daily_limit`, `follow_up.min_age_days`, `follow_up.existing_connections` (false = ignore pre-existing connections), `idle_sleep_minutes`.
- **`assets/campaign_keywords.yaml`** — Optional. Generated by `generate-keywords` subcommand. Contains `positive`, `negative`, and `exploratory` keyword lists used by the ML scorer for keyword count features and cold-start ranking.
- **`assets/templates/prompts/generate_keywords.j2`** — Jinja2 prompt template for LLM-based keyword generation. Receives `product_docs` and `campaign_objective` variables.
- **`requirements/`** — `crm.txt` (DjangoCRM, installed with `--no-deps`), `base.txt` (runtime deps, includes `analytics.txt`), `analytics.txt` (dbt-core + dbt-duckdb), `local.txt` (adds pytest/factory-boy), `production.txt`.

### Analytics Layer (dbt + DuckDB)
The `analytics/` directory contains a dbt project that reads from the CRM SQLite DB (via DuckDB's SQLite attach) to build ML training sets. No CRM data is modified.

- **`analytics/profiles.yml`** — DuckDB profile config. Attaches `assets/data/crm.db` as `crm`. Memory limit set to 2GB.
- **`analytics/models/staging/`** — Staging views over CRM tables (`stg_leads`, `stg_deals`, `stg_stages`). Lead JSON fields (including `industry_name`, `geo_name`) are parsed here.
- **`analytics/models/marts/ml_connection_accepted.sql`** — Binary classification training set: did a connection request get accepted? Target=1 (reached CONNECTED/COMPLETED), Target=0 (stuck at PENDING). Excludes DISCOVERED/ENRICHED/FAILED profiles. Uses LATERAL UNNEST CTEs to extract 24 mechanical features from positions/educations JSON arrays, plus a concatenated `profile_text` column for keyword feature extraction in Python.
- **Output:** `assets/data/analytics.duckdb` — query with `duckdb.connect("assets/data/analytics.duckdb")`.
- **Deps:** `dbt-core 1.11.x` + `dbt-duckdb 1.10.x` + `protobuf >=6,<6.32` (protobuf 6.32+ has a memory regression ~5GB RSS on startup).

### Error Handling Convention
The application should crash on unexpected errors. `try/except` blocks should only handle expected, recoverable errors. Custom exceptions in `navigation/exceptions.py`: `TerminalStateError`, `SkipProfile`, `ReachedConnectionLimit`.

### Dependencies
Core: `playwright`, `playwright-stealth`, `Django`, `django-crm-admin` (installed via `--no-deps` to skip mysqlclient), `pandas`, `langchain`/`langchain-openai`, `jinja2`, `pydantic`, `jsonpath-ng`, `tendo`, `termcolor`
ML: `scikit-learn`, `duckdb`
Analytics: `dbt-core` 1.11.x, `dbt-duckdb` 1.10.x, `protobuf` <6.32 (6.32+ has memory regression)
