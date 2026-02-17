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
- No args → runs the daemon: seeds own profile, runs GDPR location detection to auto-enable newsletter for non-GDPR jurisdictions, runs onboarding (if needed), then launches `daemon.run_daemon()` which initializes the `BayesianQualifier` (GPC, warm-started from historical labels) and time-spreads actions across configurable working hours. New profiles are auto-discovered as the daemon navigates LinkedIn pages. When all lanes are idle, LLM-generated search keywords discover new profiles.
- Any args → delegates to Django's `execute_from_command_line` (e.g. `runserver`, `migrate`, `createsuperuser`).

### Onboarding (`onboarding.py`)
Before the daemon starts, `ensure_onboarding()` ensures a Campaign, active LinkedInProfile, and LLM_API_KEY exist. Three checks:

1. **LLM_API_KEY** — if missing from `.env`, prompts user and writes it to `.env`.
2. **Campaign** — if no `Campaign` exists in DB, runs interactive prompts for campaign name, product docs, campaign objective, booking link. Creates `Department` + `Campaign`.
3. **LinkedInProfile** — if no active profile exists, prompts for LinkedIn email, password, newsletter preference, and rate limits. Creates `User` + `LinkedInProfile`.

### Profile State Machine
Each profile progresses through states defined in `navigation/enums.py:ProfileState`:
`DISCOVERED` → `ENRICHED` → `QUALIFIED` → `PENDING` → `CONNECTED` → `COMPLETED` (or `FAILED` / `DISQUALIFIED`)

States map to DjangoCRM Deal Stages (defined in `db/crm_profiles.py:STATE_TO_STAGE`).

The daemon (`daemon.py`) runs within configurable working hours (default 09:00–18:00, OS local timezone). Three **major lanes** fire at a fixed pace set by `min_action_interval` (default 120s, ±20% random jitter). Daily/weekly rate limiters independently cap totals. **Enrichment** and **qualification** dynamically fill the gaps between major actions (`gap / total_work`, floored at `enrich_min_interval`). Outside working hours the daemon sleeps until the next window starts.

1. **Connect** (scheduled, highest priority) — ML-ranks QUALIFIED profiles, sends connection request → PENDING. Interval = `min_action_interval`. Pre-existing connections detected at connect time are marked CONNECTED (flow through normal pipeline).
2. **Check Pending** (scheduled) — checks PENDING profiles for acceptance → CONNECTED. Uses exponential backoff per profile: initial interval = `check_pending_recheck_after_hours` (default 24h), doubles each time a profile is still pending.
3. **Follow Up** (scheduled) — sends follow-up message to CONNECTED profiles → COMPLETED. Contacts profiles immediately once discovered as connected. Interval = `min_action_interval`.
4. **Enrich** (gap-filling) — scrapes 1 DISCOVERED profile per tick via Voyager API → ENRICHED. Computes and stores embedding after enrichment. Paced to fill time between major actions.
5. **Qualify** (gap-filling) — two-phase: (1) embeds ENRICHED profiles that lack embeddings, (2) qualifies embedded profiles using GPC active learning — BALD (via MC sampling from GP latent posterior) selects the most informative candidate, predictive entropy gates auto-decisions vs LLM queries → QUALIFIED or DISQUALIFIED. Model lazily re-fitted on all accumulated labels when predictions are needed.
6. **Search** (lowest-priority gap-filler) — fires only when enrich + qualify have nothing to do. Uses LLM-generated LinkedIn People search keywords (from campaign context via `search_keywords.j2`) to discover new profiles. Pops one keyword per execution; refills from LLM when exhausted.

The `DISQUALIFIED` state is a terminal state for profiles rejected by the qualification pipeline.

### Qualification ML Pipeline

The qualification lane uses a **Gaussian Process Classifier** (sklearn, RBF kernel) with BALD active learning:

1. **BALD selects** — Which profile to evaluate next. `bald_scores()` extracts the GP latent posterior mean and variance (`f_mean`, `f_var`) from the fitted model's internals, draws MC samples `f ~ N(f_mean, f_var)`, pushes through sigmoid, and computes `BALD = H(E[p]) - E[H(p)]`. High BALD means the model's posterior *disagrees with itself* about a candidate — labelling it would maximally reduce model uncertainty. This avoids wasting LLM calls on genuinely ambiguous profiles (high entropy but low BALD).

2. **Predictive entropy gates** — How to decide on the selected profile. Predictive entropy `H(p)` from `predict()` determines whether the model is confident enough to auto-decide or must defer to the LLM:
   - `entropy < entropy_threshold` and model is fitted → **auto-decide** (prob >= 0.5 → accept, else reject)
   - Otherwise → **LLM query** via `qualify_lead.j2` prompt

The GPC uses a `ConstantKernel * RBF` kernel on 384-dim FastEmbed embeddings. Training data is accumulated incrementally; the model is lazily re-fitted (on ALL data, O(n³)) whenever predictions are requested after new labels arrive. Previously-fitted kernel params seed the optimizer for fast refits. On daemon restart, `warm_start()` bulk-loads historical labels and fits once.

Cold start (< 2 labels or single class) returns `None` from `predict`/`bald_scores`, and the qualify lane logs and defers to the LLM. As labels accumulate, the GPC progressively auto-decides more profiles, reducing LLM calls.

### CRM Data Model
- **Campaign** (`linkedin.models.Campaign`) — 1:1 with `common.Department`. Stores `product_docs`, `campaign_objective`, `followup_template` (Jinja2 prompt content), `booking_link`.
- **LinkedInProfile** (`linkedin.models.LinkedInProfile`) — 1:1 with `auth.User`, FK to Campaign. Stores `linkedin_username`, `linkedin_password`, `subscribe_newsletter`, `active`, `connect_daily_limit`, `connect_weekly_limit`, `follow_up_daily_limit`.
- **Lead** — Created per LinkedIn profile URL. Stores `first_name`, `last_name`, `title`, `website` (LinkedIn URL), `description` (full parsed profile JSON).
- **Contact** — Created after enrichment, linked to Company.
- **Company** — Created from first position's company name.
- **Deal** — Tracks pipeline stage. One Deal per Lead. Stage maps to ProfileState. `next_step` field stores JSON metadata (e.g. `{"backoff_hours": N}` for exponential backoff in check_pending).
- **TheFile** — Raw Voyager API JSON attached to Lead via GenericForeignKey.

### Key Modules
- **`models.py`** — Django models: `Campaign` (1:1 with Department; product_docs, campaign_objective, followup_template, booking_link) and `LinkedInProfile` (1:1 with User; credentials, rate limits, newsletter preference). Registered in `admin.py`.
- **`daemon.py`** — Main daemon loop. Creates `BayesianQualifier` (GPC, warm-started from historical labels), rate limiters (from `LinkedInProfile` model), `LaneSchedule` objects for three major lanes, qualify lane for gap-filling, and a search lane as lowest-priority gap-filler. Spreads actions across working hours; qualifications dynamically fill gaps between scheduled major actions. Initializes embeddings table at startup.
- **`lanes/`** — Action lanes executed by the daemon:
  - `qualify.py` — Two-phase qualification lane: (1) embeds ENRICHED profiles that lack embeddings (backfill), (2) qualifies embedded profiles via GPC active learning — BALD selects the most informative candidate, predictive entropy gates auto-decisions (low entropy → auto-accept/reject, high entropy or model unfitted → LLM query via `qualify_lead.j2` prompt) → QUALIFIED or DISQUALIFIED. Reads `product_docs`/`campaign_objective` from `session.campaign`.
  - `connect.py` — Ranks QUALIFIED profiles by GPC predictive probability (via `BayesianQualifier.rank_profiles()`), sends connection requests. Pre-existing connections always flow through as CONNECTED.
  - `check_pending.py` — Checks PENDING profiles for acceptance. Uses exponential backoff: doubles `backoff_hours` in `deal.next_step` each time a profile is still pending.
  - `follow_up.py` — Sends follow-up messages to CONNECTED profiles.
  - `search.py` — Lowest-priority gap-filler. Fires only when enrich + qualify have nothing to do. Uses LLM-generated LinkedIn People search keywords (via `ml/search_keywords.py`) to discover new profiles. Reads campaign content from `session.campaign`.
- **`ml/embeddings.py`** — DuckDB store for profile embeddings. Uses `fastembed` (BAAI/bge-small-en-v1.5 by default) for 384-dim embeddings. Functions: `embed_text()`, `embed_texts()`, `embed_profile()`, `store_embedding()`, `store_label()`, `get_all_unlabeled_embeddings()`, `get_unlabeled_profiles()`, `get_labeled_data()`, `count_labeled()`, `get_embedded_lead_ids()`, `ensure_embeddings_table()`.
- **`ml/qualifier.py:BayesianQualifier`** — GaussianProcessClassifier (sklearn, ConstantKernel * RBF) with lazy refit. `update(embedding, label)` appends to training data and invalidates fit. `predict(embedding)` returns `(prob, entropy)` or `None` if unfitted. `bald_scores(embeddings)` computes BALD via MC sampling from GP latent posterior, returns array or `None` if unfitted. `rank_profiles(profiles)` sorts by predicted probability (descending). `warm_start(X, y)` bulk-loads historical labels and fits once. Also exports `qualify_profile_llm(profile_text, product_docs, campaign_objective)` for LLM-based lead qualification with structured output.
- **`ml/profile_text.py`** — `build_profile_text()`: concatenates all text fields from profile dict (headline, summary, positions, educations, etc.), lowercased. Used for embedding input.
- **`ml/search_keywords.py`** — `generate_search_keywords(product_docs, campaign_objective)`: calls LLM via `search_keywords.j2` prompt to generate LinkedIn People search queries.
- **`rate_limiter.py:RateLimiter`** — Daily/weekly rate limits with auto-reset. Supports external exhaustion (LinkedIn-side limits).
- **`sessions/account.py:AccountSession`** — Central session object. Loads `LinkedInProfile` from DB, exposes `linkedin_profile`, `campaign`, `django_user`, `account_cfg` dict, and Playwright browser. Passed throughout the codebase.
- **`db/crm_profiles.py`** — Profile CRUD backed by DjangoCRM models. `get_profile()` returns a plain dict with `state` and `profile` keys. Includes `get_qualified_profiles()`, `get_pending_profiles()` (per-profile exponential backoff via `deal.next_step`), `get_connected_profiles()` for lane queries. `_deal_to_profile_dict()` includes a `meta` key with parsed `next_step` JSON. `set_profile_state()` clears `next_step` on any transition to/from PENDING.
- **`gdpr.py`** — GDPR location detection for newsletter auto-subscription. Checks LinkedIn country code against a static set of ISO-2 codes for opt-in email marketing jurisdictions (EU/EEA, UK, Switzerland, Canada, Brazil, Australia, Japan, South Korea, New Zealand). Missing/None codes default to protected. `apply_gdpr_newsletter_override()` updates `LinkedInProfile.subscribe_newsletter` in DB for non-GDPR locations.
- **`onboarding.py`** — DB-backed onboarding. `ensure_onboarding()` ensures LLM_API_KEY in `.env`, Campaign in DB, and active LinkedInProfile in DB. If missing, prompts user interactively. Creates Django models directly.
- **`conf.py`** — Loads `LLM_API_KEY` from `.env`. Exports `CAMPAIGN_CONFIG` dict (timing and ML defaults as Python constants), `AI_MODEL`, `LLM_API_BASE`, path constants. `get_first_active_profile_handle()` queries `LinkedInProfile` model.
- **`api/voyager.py`** — Parses LinkedIn's Voyager API JSON responses into clean dicts via internal dataclasses (`LinkedInProfile`, `Position`, `Education`). Uses URN reference resolution from the `included` array.
- **`django_settings.py`** — Django settings importing DjangoCRM's default settings. SQLite DB at `assets/data/crm.db`.
- **`management/setup_crm.py`** — Idempotent bootstrap: creates Department, Deal Stages, ClosingReasons, LeadSource, and default Campaign (with followup template seeded from hardcoded content).
- **`templates/renderer.py`** — Renders followup template from `session.campaign.followup_template` string via `jinja2.Environment().from_string()`, then passes through LLM. Appends `booking_link` from campaign. Product description injected from `session.campaign.product_docs`.
- **`navigation/`** — Login flow, throttling, browser utilities. `utils.py` always extracts `/in/` profile URLs from pages visited (auto-discovery).
- **`actions/`** — Individual browser actions (scrape, connect, message, search).

### Configuration
- **`.env`** — `LLM_API_KEY` (required, prompted during onboarding if missing). Optionally `LLM_API_BASE`, `AI_MODEL`.
- **`conf.py:CAMPAIGN_CONFIG`** — Hardcoded timing/ML defaults:
  - `check_pending_recheck_after_hours` (24), `working_hours_start` ("09:00"), `working_hours_end` ("18:00")
  - `enrich_min_interval` (1), `min_action_interval` (120)
  - `qualification_entropy_threshold` (0.3), `qualification_n_mc_samples` (100), `embedding_model` ("BAAI/bge-small-en-v1.5")
- **Campaign model** — `product_docs`, `campaign_objective`, `followup_template`, `booking_link` — managed via Django Admin or onboarding.
- **LinkedInProfile model** — `linkedin_username`, `linkedin_password`, `subscribe_newsletter`, `active`, `connect_daily_limit` (20), `connect_weekly_limit` (100), `follow_up_daily_limit` (30) — managed via Django Admin or onboarding.
- **`assets/templates/prompts/qualify_lead.j2`** — Jinja2 prompt template for LLM-based lead qualification. Receives `product_docs`, `campaign_objective`, and `profile_text` variables.
- **`assets/templates/prompts/search_keywords.j2`** — Jinja2 prompt template for LLM-based search keyword generation. Receives `product_docs`, `campaign_objective`, and `n_keywords` variables.
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
ML/Embeddings: `scikit-learn` (GaussianProcessClassifier), `numpy`, `duckdb`, `fastembed`
Analytics: `dbt-core` 1.11.x, `dbt-duckdb` 1.10.x, `protobuf` 6.33.x (6.32.x had memory regression, resolved in 6.33+)
