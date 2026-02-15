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
python manage.py                     # run the daemon (interactive onboarding on first run)
python manage.py runserver           # Django Admin at http://localhost:8000/admin/
python manage.py migrate             # run Django migrations
python manage.py createsuperuser     # create Django admin user
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
`manage.py` (Django bootstrap + auto-migrate + CRM setup):
- No args → runs the daemon: seeds own profile, runs GDPR location detection to auto-enable newsletter for non-GDPR jurisdictions, runs onboarding (if needed), then launches `daemon.run_daemon()` which initializes the `BayesianQualifier` (warm-started from historical labels) and time-spreads actions across configurable working hours. New profiles are auto-discovered as the daemon navigates LinkedIn pages.
- Any args → delegates to Django's `execute_from_command_line` (e.g. `runserver`, `migrate`, `createsuperuser`).

### Onboarding (`onboarding.py`)
Before the daemon starts, `ensure_onboarding()` ensures product docs and campaign objective exist. Two paths:

1. **Already onboarded** — if `assets/campaign/product_docs.txt` and `assets/campaign/campaign_objective.txt` both exist, does nothing.
2. **Interactive onboarding** — if either file is missing, prompts the user in the terminal:
   - Product/service description (multi-line, Ctrl-D to finish)
   - Campaign objective (multi-line, Ctrl-D to finish)
   - Validates non-empty, persists inputs to `assets/campaign/product_docs.txt` and `assets/campaign/campaign_objective.txt`.

Persisted files under `assets/campaign/`: `product_docs.txt`, `campaign_objective.txt`.

### Profile State Machine
Each profile progresses through states defined in `navigation/enums.py:ProfileState`:
`DISCOVERED` → `ENRICHED` → `QUALIFIED` → `PENDING` → `CONNECTED` → `COMPLETED` (or `FAILED` / `IGNORED` / `DISQUALIFIED`)

States map to DjangoCRM Deal Stages (defined in `db/crm_profiles.py:STATE_TO_STAGE`).

The daemon (`daemon.py`) spreads actions across configurable working hours (default 09:00–18:00, OS local timezone). Three **major lanes** are scheduled at fixed intervals derived from `active_hours / daily_limit` (±20% random jitter). **Enrichment** and **qualification** dynamically fill the gaps between major actions (`gap / total_work`, floored at `enrich_min_interval`). Outside working hours the daemon sleeps until the next window starts.

1. **Connect** (scheduled, highest priority) — ML-ranks QUALIFIED profiles, sends connection request → PENDING. Interval = active_minutes / connect_daily_limit.
2. **Check Pending** (scheduled) — checks PENDING profiles for acceptance → CONNECTED. Uses exponential backoff per profile: initial interval = `check_pending_recheck_after_hours` (default 24h), doubles each time a profile is still pending.
3. **Follow Up** (scheduled) — sends follow-up message to CONNECTED profiles → COMPLETED. Contacts profiles immediately once discovered as connected. Interval = active_minutes / follow_up_daily_limit.
4. **Enrich** (gap-filling) — scrapes 1 DISCOVERED profile per tick via Voyager API → ENRICHED (or IGNORED if pre-existing connection). Computes and stores embedding after enrichment. Paced to fill time between major actions.
5. **Qualify** (gap-filling) — two-phase: (1) embeds ENRICHED profiles that lack embeddings, (2) qualifies embedded profiles using Bayesian active learning — BALD acquisition selects the most informative candidate, predictive entropy gates auto-decisions vs LLM queries → QUALIFIED or DISQUALIFIED. Model updates online on every label (no batch retraining).

The `IGNORED` state is a terminal state for pre-existing connections (already connected before the automation ran). Controlled by `follow_up_existing_connections` config flag. The `DISQUALIFIED` state is a terminal state for profiles rejected by the qualification pipeline.

### Qualification ML Pipeline

The qualification lane uses **Online Bayesian Logistic Regression** with two separate concerns:

1. **BALD selects** — Which profile to evaluate next. BALD (Bayesian Active Learning by Disagreement) measures how much the model's posterior weight samples *disagree* about a candidate. High BALD means labeling that profile would maximally reduce model uncertainty. This is distinct from predictive uncertainty: a profile at the decision boundary can have low BALD if all posterior samples agree it's ~50/50.

2. **Predictive entropy gates** — How to decide on the selected profile. Once BALD picks the most informative candidate, predictive entropy H(p_pred) determines whether the model is confident enough to auto-decide or must defer to the LLM:
   - `entropy < entropy_threshold` and `n_obs > 0` → **auto-decide** (prob >= 0.5 → accept, else reject)
   - Otherwise → **LLM query** via `qualify_lead.j2` prompt

The model maintains a Gaussian posterior N(μ, Σ) over w ∈ R^385 (384 embedding dims + 1 bias). Each label triggers a rank-1 Sherman-Morrison covariance update — O(d²), no batch retraining. On daemon restart, `warm_start()` replays all historical labels to restore the posterior.

Cold start (n_obs = 0) always defers to the LLM. As labels accumulate, the model progressively auto-decides more profiles, reducing LLM calls.

### CRM Data Model
- **Lead** — Created per LinkedIn profile URL. Stores `first_name`, `last_name`, `title`, `website` (LinkedIn URL), `description` (full parsed profile JSON).
- **Contact** — Created after enrichment, linked to Company.
- **Company** — Created from first position's company name.
- **Deal** — Tracks pipeline stage. One Deal per Lead. Stage maps to ProfileState. `next_step` field stores JSON metadata (e.g. `{"backoff_hours": N}` for exponential backoff in check_pending).
- **TheFile** — Raw Voyager API JSON attached to Lead via GenericForeignKey.

### Key Modules
- **`daemon.py`** — Main daemon loop. Creates `BayesianQualifier` (warm-started from historical labels), rate limiters, `LaneSchedule` objects for three major lanes, and enrich + qualify lanes for gap-filling. Spreads actions across working hours; enrichments and qualifications dynamically fill gaps between scheduled major actions. Initializes embeddings table at startup.
- **`lanes/`** — Action lanes executed by the daemon:
  - `enrich.py` — Scrapes 1 DISCOVERED profile per tick via Voyager API. Detects pre-existing connections (IGNORED). Computes and stores embedding after enrichment. Exports `is_preexisting_connection()` shared helper.
  - `qualify.py` — Two-phase qualification lane: (1) embeds ENRICHED profiles that lack embeddings (backfill), (2) qualifies embedded profiles via Bayesian active learning — BALD selects the most informative candidate, predictive entropy gates auto-decisions (low entropy → auto-accept/reject, high entropy → LLM query via `qualify_lead.j2` prompt) → QUALIFIED or DISQUALIFIED. Online model updates on every label.
  - `connect.py` — Ranks QUALIFIED profiles by posterior predictive probability (via `BayesianQualifier.rank_profiles()`), sends connection requests. Catches pre-existing connections missed by enrich (when `connection_degree` was None at scrape time).
  - `check_pending.py` — Checks PENDING profiles for acceptance. Uses exponential backoff: doubles `backoff_hours` in `deal.next_step` each time a profile is still pending.
  - `follow_up.py` — Sends follow-up messages to CONNECTED profiles.
- **`ml/embeddings.py`** — DuckDB store for profile embeddings. Uses `fastembed` (BAAI/bge-small-en-v1.5 by default) for 384-dim embeddings. Functions: `embed_text()`, `embed_texts()`, `embed_profile()`, `store_embedding()`, `store_label()`, `get_all_unlabeled_embeddings()`, `get_unlabeled_profiles()`, `get_labeled_data()`, `count_labeled()`, `get_embedded_lead_ids()`, `ensure_embeddings_table()`.
- **`ml/qualifier.py:BayesianQualifier`** — Online Bayesian Logistic Regression with Laplace approximation. Maintains Gaussian posterior N(μ, Σ) over w ∈ R^385 (384 embedding + 1 bias). `update(embedding, label)` performs rank-1 Sherman-Morrison covariance update (O(d²) per observation). `predict(embedding)` returns (predictive_prob, BALD_score) via MC sampling from posterior. `bald_scores(embeddings)` computes vectorized BALD for candidate selection. `rank_profiles(profiles)` sorts by posterior predictive probability (descending). `warm_start(X, y)` replays historical labels on daemon restart. Also exports `qualify_profile_llm()` for LLM-based lead qualification with structured output.
- **`ml/profile_text.py`** — `build_profile_text()`: concatenates all text fields from profile dict (headline, summary, positions, educations, etc.), lowercased. Used for embedding input.
- **`rate_limiter.py:RateLimiter`** — Daily/weekly rate limits with auto-reset. Supports external exhaustion (LinkedIn-side limits).
- **`sessions/account.py:AccountSession`** — Central session object holding Playwright browser, Django User, and account config. Passed throughout the codebase.
- **`db/crm_profiles.py`** — Profile CRUD backed by DjangoCRM models. `get_profile()` returns a plain dict with `state` and `profile` keys. Includes `get_enriched_profiles()`, `get_qualified_profiles()`, `get_pending_profiles()` (per-profile exponential backoff via `deal.next_step`), `get_connected_profiles()` for lane queries. `_deal_to_profile_dict()` includes a `meta` key with parsed `next_step` JSON. `set_profile_state()` clears `next_step` on any transition to/from PENDING.
- **`gdpr.py`** — GDPR location detection for newsletter auto-subscription. Checks LinkedIn country code against a static set of ISO-2 codes for opt-in email marketing jurisdictions (EU/EEA, UK, Switzerland, Canada, Brazil, Australia, Japan, South Korea, New Zealand). Missing/None codes default to protected. `apply_gdpr_newsletter_override()` auto-enables `subscribe_newsletter` for non-GDPR locations.
- **`onboarding.py`** — Interactive onboarding. `ensure_onboarding()` checks for `product_docs.txt` and `campaign_objective.txt`; if missing, prompts user interactively. `_read_multiline()` reads multi-line terminal input (Ctrl-D to finish).
- **`conf.py`** — Loads config from `.env` and `assets/accounts.secrets.yaml`. Exports `CAMPAIGN_CONFIG` dict (rate limits, timing, qualification thresholds, embedding model), `PRODUCT_DOCS_FILE` / `CAMPAIGN_OBJECTIVE_FILE` / `EMBEDDINGS_DB` paths. `LLM_API_KEY` is required (raises `ValueError` if missing). All paths derived from `ASSETS_DIR`.
- **`api/voyager.py`** — Parses LinkedIn's Voyager API JSON responses into clean dicts via internal dataclasses (`LinkedInProfile`, `Position`, `Education`). Uses URN reference resolution from the `included` array.
- **`django_settings.py`** — Django settings importing DjangoCRM's default settings. SQLite DB at `assets/data/crm.db`.
- **`management/setup_crm.py`** — Idempotent bootstrap: creates Department, Users, Deal Stages (including Qualified, Disqualified, Ignored), ClosingReasons (including Disqualified), LeadSource.
- **`templates/renderer.py`** — Jinja2 or AI-prompt-based message rendering. Template type (`jinja` or `ai_prompt`) configured per account. AI calls go through LangChain.
- **`navigation/`** — Login flow, throttling, browser utilities. `utils.py` always extracts `/in/` profile URLs from pages visited (auto-discovery).
- **`actions/`** — Individual browser actions (scrape, connect, message, search).

### Configuration
- **`assets/accounts.secrets.yaml`** — Single config file containing: `env:` (API keys, model — `LLM_API_KEY` is **required**), `campaign:` (rate limits, timing, qualification thresholds, feature flags), `accounts:` (credentials, template). Copy from `assets/accounts.secrets.template.yaml`.
- **`env:` section** — `LLM_API_KEY` (required), `LLM_API_BASE` (optional), `AI_MODEL` (default `gpt-5.3-codex`).
- **`campaign:` section** — `connect.daily_limit`, `connect.weekly_limit`, `check_pending.recheck_after_hours` (base interval, doubles per profile via exponential backoff), `follow_up.daily_limit`, `follow_up.existing_connections` (false = ignore pre-existing connections), `enrich_min_interval` (floor seconds between enrichment API calls, default 1), `working_hours.start` / `working_hours.end` (HH:MM, default 09:00–18:00, OS local timezone).
- **`campaign.qualification:` section** — `entropy_threshold` (default 0.3, predictive entropy below which model auto-decides without LLM), `prior_precision` (default 1.0, higher = more conservative Bayesian prior), `n_mc_samples` (default 100, Monte Carlo samples for BALD computation), `embedding_model` (default `BAAI/bge-small-en-v1.5`).
- **`assets/campaign/product_docs.txt`** — Persisted product/service description from onboarding. Used by the LLM qualification prompt.
- **`assets/campaign/campaign_objective.txt`** — Persisted campaign objective from onboarding. Used by the LLM qualification prompt.
- **`assets/templates/prompts/qualify_lead.j2`** — Jinja2 prompt template for LLM-based lead qualification. Receives `product_docs`, `campaign_objective`, and `profile_text` variables.
- **`requirements/`** — `crm.txt` (DjangoCRM, installed with `--no-deps`), `base.txt` (runtime deps, includes `analytics.txt`), `analytics.txt` (dbt-core + dbt-duckdb), `local.txt` (adds pytest/factory-boy), `production.txt`.

### Analytics Layer (dbt + DuckDB)
The `analytics/` directory contains a dbt project that reads from the CRM SQLite DB (via DuckDB's SQLite attach) to build ML training sets. No CRM data is modified.

- **`analytics/profiles.yml`** — DuckDB profile config. Attaches `assets/data/crm.db` as `crm`. Memory limit set to 2GB.
- **`analytics/models/staging/`** — Staging views over CRM tables (`stg_leads`, `stg_deals`, `stg_stages`). Lead JSON fields (including `industry_name`, `geo_name`) are parsed here.
- **`analytics/models/marts/ml_connection_accepted.sql`** — Binary classification training set: did a connection request get accepted? Target=1 (reached CONNECTED/COMPLETED), Target=0 (stuck at PENDING). Excludes DISCOVERED/ENRICHED/FAILED profiles. Uses LATERAL UNNEST CTEs to extract 24 mechanical features from positions/educations JSON arrays, plus a concatenated `profile_text` column for keyword feature extraction in Python.
- **Output:** `assets/data/analytics.duckdb` — query with `duckdb.connect("assets/data/analytics.duckdb")`.
- **Deps:** `dbt-core 1.11.x` + `dbt-duckdb 1.10.x` + `protobuf` 6.33.x (pinned; earlier 6.32.x had a memory regression ~5GB RSS on startup, resolved in 6.33+).

### Error Handling Convention
The application should crash on unexpected errors. `try/except` blocks should only handle expected, recoverable errors. Custom exceptions in `navigation/exceptions.py`: `AuthenticationError`, `TerminalStateError`, `SkipProfile`, `ReachedConnectionLimit`.

### Dependencies
Core: `playwright`, `playwright-stealth`, `Django`, `django-crm-admin` (installed via `--no-deps` to skip mysqlclient), `pandas`, `langchain`/`langchain-openai`, `jinja2`, `pydantic`, `jsonpath-ng`, `tendo`, `termcolor`
ML/Embeddings: `numpy`, `duckdb`, `fastembed`
Analytics: `dbt-core` 1.11.x, `dbt-duckdb` 1.10.x, `protobuf` 6.33.x (6.32.x had memory regression, resolved in 6.33+)
