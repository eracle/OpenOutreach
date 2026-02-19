# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Documentation Rule

When modifying code, always update CLAUDE.md and MEMORY.md to reflect the changes. This includes changes to models, function signatures, module structure, configuration keys, state machines, lane behavior, ML pipeline, and any other architectural details documented in these files. Documentation must stay in sync with the code at all times.

## Dependency Rule

When adding, removing, or changing a dependency in `requirements/*.txt`, always mirror the change in `pyproject.toml` (and vice versa). The two sources must stay in sync — `pyproject.toml` is used for local dev via `uv`, `requirements/` files are used by Docker.

## Project Overview

OpenOutreach is a self-hosted LinkedIn automation tool for B2B lead generation. It uses Playwright with stealth plugins for browser automation and LinkedIn's internal Voyager API for structured profile data. The CRM backend is powered by DjangoCRM with Django Admin UI.

## Commands

### Local Development (requires [uv](https://docs.astral.sh/uv/))
```bash
make setup                           # install deps + Playwright browsers + migrate + bootstrap CRM
make run                             # run the daemon (interactive onboarding on first run)
make admin                           # Django Admin at http://localhost:8000/admin/
make analytics                       # build dbt models (DuckDB analytics)
make analytics-test                  # run dbt schema tests
uv run python manage.py migrate      # run Django migrations
uv run python manage.py createsuperuser  # create Django admin user
```

### Testing
```bash
make test                         # run tests locally
make docker-test                  # run tests in Docker
uv run pytest tests/api/test_voyager.py  # run single test file
uv run pytest -k test_name              # run single test by name
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
- No args → runs the daemon: seeds own profile (disqualified + `/in/me/` sentinel), runs GDPR location detection to auto-enable newsletter for non-GDPR jurisdictions (guarded by marker file), runs onboarding (if needed), then launches `daemon.run_daemon()` which initializes the `BayesianQualifier` (GPR pipeline, warm-started from historical labels) and spreads actions at a configurable pace across multiple campaigns. New profiles are auto-discovered as the daemon navigates LinkedIn pages. When all lanes are idle, LLM-generated search keywords discover new profiles.
- Any args → delegates to Django's `execute_from_command_line` (e.g. `runserver`, `migrate`, `createsuperuser`).

### Onboarding (`onboarding.py`)
Before the daemon starts, `ensure_onboarding()` ensures a Campaign, active LinkedInProfile, and LLM config exist. Three checks:

1. **LLM config** — if missing from `.env`, prompts user for `LLM_API_KEY` (required), `AI_MODEL` (required), and `LLM_API_BASE` (optional), and writes them to `.env`.
2. **Campaign** — if no `Campaign` exists in DB, runs interactive prompts for campaign name, product docs, campaign objective, booking link. Creates `Department` + `Campaign` (followup template seeded from `followup2.j2`).
3. **LinkedInProfile** — if no active profile exists, prompts for LinkedIn email, password, newsletter preference, and rate limits. Creates `User` + `LinkedInProfile`.

### Profile State Machine
The `navigation/enums.py:ProfileState` enum defines Deal-level states: `NEW`, `PENDING`, `CONNECTED`, `COMPLETED`, `FAILED`. Pre-Deal states are implicit: a Lead with no description is "url_only" (discovered), a Lead with description is "enriched", a Lead with `disqualified=True` is disqualified. Promotion from Lead to Contact+Deal happens when qualification passes.

Deal stages map via `db/crm_profiles.py:STATE_TO_STAGE`: `NEW→New`, `PENDING→Pending`, `CONNECTED→Connected`, `COMPLETED→Completed`, `FAILED→Failed`.

The daemon (`daemon.py`) runs continuously with multi-campaign support. Each campaign gets its own `LaneSchedule` objects for three **major lanes** that fire at a fixed pace set by `min_action_interval` (default 120s, ±20% random jitter). Daily/weekly rate limiters independently cap totals (shared across campaigns). **Qualification** and **search** dynamically fill gaps between major actions (non-partner campaigns only). Partner campaigns use probabilistic gating via `Campaign.action_fraction`. Regular campaigns are inversely gated: when a partner campaign exists, regular actions are skipped with probability = `action_fraction` (so at 1.0, only partner campaigns run).

1. **Connect** (scheduled, highest priority) — ML-ranks qualified profiles by GPR predicted probability (via `BayesianQualifier.rank_profiles()`), sends connection request → PENDING. Interval = `min_action_interval`. Pre-existing connections detected at connect time are marked CONNECTED.
2. **Check Pending** (scheduled) — checks PENDING profiles for acceptance → CONNECTED. Uses exponential backoff per profile: initial interval = `check_pending_recheck_after_hours` (default 24h), doubles each time a profile is still pending.
3. **Follow Up** (scheduled) — sends follow-up message to CONNECTED profiles → COMPLETED. Contacts profiles immediately once discovered as connected. Interval = `min_action_interval`.
4. **Qualify** (gap-filling) — two-phase: (1) embeds enriched profiles that lack embeddings, (2) qualifies embedded profiles using GPR active learning with balance-driven explore/exploit — when negatives outnumber positives, exploits (picks highest predicted probability); otherwise, explores (picks highest BALD score). Predictive entropy + posterior std gate auto-decisions vs LLM queries → qualified or disqualified. Model lazily re-fitted on all accumulated labels when predictions are needed.
5. **Search** (lowest-priority gap-filler) — fires only when qualify has nothing to do and `pipeline_needs_refill()` is True. Uses LLM-generated LinkedIn People search keywords (via `search_keywords.j2`, persisted in `SearchKeyword` model). Pops one keyword per execution; refills from LLM when exhausted (passing used keywords as `exclude_keywords`).

### Qualification ML Pipeline

The qualification lane uses a **Gaussian Process Regressor** (sklearn, ConstantKernel * RBF) inside a `Pipeline(PCA, StandardScaler, GPR)` with BALD active learning:

1. **Balance-driven selection** — Which profile to evaluate next depends on label balance:
   - If `n_negatives > n_positives` → **exploit**: pick highest predicted probability (`predicted_probs()`)
   - Otherwise → **explore**: pick highest BALD score (`bald_scores()`, MC sampling from GP posterior)

2. **Auto-decision gate** — How to decide on the selected profile. `predict()` returns `(prob, entropy, posterior_std)`. Two conditions must both hold for auto-decision:
   - `entropy < entropy_threshold` (default 0.3)
   - `posterior_std < max_auto_std` (default 0.05)
   - Auto-accept additionally requires `prob >= min_accept_prob` (default 0.9)
   - Otherwise → **LLM query** via `qualify_lead.j2` prompt

The pipeline uses PCA (dimension selected via LML cross-validation over candidates `{2,4,6,10,15,20}`) + StandardScaler + GPR on 384-dim FastEmbed embeddings. Training data is accumulated incrementally; the model is lazily re-fitted (on ALL data, O(n³)) whenever predictions are requested after new labels arrive. The fitted pipeline is persisted via `joblib` to `MODEL_PATH`. On daemon restart, `warm_start()` bulk-loads historical labels and fits once.

Cold start (< 2 labels or single class) returns `None` from `predict`/`bald_scores`, and the qualify lane defers to the LLM. As labels accumulate, the GPR progressively auto-decides more profiles, reducing LLM calls.

### CRM Data Model
- **Campaign** (`linkedin.models.Campaign`) — 1:1 with `common.Department`. Stores `product_docs`, `campaign_objective`, `followup_template` (Jinja2 prompt content), `booking_link`, `is_partner` (bool), `action_fraction` (float, probabilistic gating for partner campaigns).
- **LinkedInProfile** (`linkedin.models.LinkedInProfile`) — 1:1 with `auth.User`. Stores `linkedin_username`, `linkedin_password`, `subscribe_newsletter`, `active`, `connect_daily_limit`, `connect_weekly_limit`, `follow_up_daily_limit`. Campaign membership is via Django group (User → Group → Department → Campaign).
- **SearchKeyword** (`linkedin.models.SearchKeyword`) — FK to Campaign. Stores `keyword`, `used` (bool), `used_at`. Unique together on `(campaign, keyword)`. Persists LLM-generated search keywords across restarts.
- **Lead** — Created per LinkedIn profile URL. Stores `first_name`, `last_name`, `title`, `website` (LinkedIn URL), `description` (full parsed profile JSON), `disqualified` (bool).
- **Contact** — Created after qualification (promotion from Lead), linked to Company.
- **Company** — Created from first position's company name.
- **Deal** — Tracks pipeline stage. One Deal per Contact. Stage maps to ProfileState. `next_step` field stores JSON metadata (e.g. `{"backoff_hours": N}` for exponential backoff in check_pending).
- **TheFile** — Raw Voyager API JSON attached to Lead via GenericForeignKey.

### Key Modules
- **`models.py`** — Django models: `Campaign` (1:1 with Department; product_docs, campaign_objective, followup_template, booking_link, is_partner, action_fraction), `LinkedInProfile` (1:1 with User; credentials, rate limits, newsletter preference), and `SearchKeyword` (FK to Campaign; keyword, used, used_at). Registered in `admin.py`.
- **`daemon.py`** — Main daemon loop. Creates `BayesianQualifier` (GPR pipeline, warm-started from historical labels), rate limiters (from `LinkedInProfile` model, shared across campaigns), `LaneSchedule` objects per campaign for three major lanes, qualify lane for gap-filling, and a search lane as lowest-priority gap-filler. Supports multi-campaign architecture: iterates `session.campaigns`, creates separate schedules per campaign, with shared qualifier/rate limiters. Partner campaigns use probabilistic gating (`action_fraction`). Initializes embeddings table at startup. Also imports partner campaigns via `ml/hub.py`.
- **`lanes/`** — Action lanes executed by the daemon:
  - `qualify.py` — Two-phase qualification lane: (1) embeds enriched profiles that lack embeddings (backfill), (2) qualifies embedded profiles via GPR active learning — balance-driven explore/exploit (more negatives → exploit highest prob, otherwise → explore highest BALD). Auto-decision requires both `entropy < threshold` AND `std < max_auto_std`; auto-accept also requires `prob >= min_accept_prob`. Otherwise → LLM query via `qualify_lead.j2` prompt. Reads `product_docs`/`campaign_objective` from `session.campaign`.
  - `connect.py` — Ranks qualified profiles by GPR predictive probability (via `BayesianQualifier.rank_profiles()`), sends connection requests. Accepts optional `pipeline` kwarg for partner campaigns. Pre-existing connections always flow through as CONNECTED.
  - `check_pending.py` — Checks PENDING profiles for acceptance. Iterates ALL ready profiles per tick. Uses exponential backoff: doubles `backoff_hours` in `deal.next_step` each time a profile is still pending.
  - `follow_up.py` — Sends follow-up messages to CONNECTED profiles. Processes one profile per tick.
  - `search.py` — Lowest-priority gap-filler. Fires only when qualify has nothing to do and pipeline needs refill. Keywords persisted in `SearchKeyword` model (DB-backed, survives restarts). Passes `exclude_keywords` (already used) to LLM refill. Uses `bulk_create(ignore_conflicts=True)`.
- **`ml/embeddings.py`** — DuckDB store for profile embeddings. Uses `fastembed` (BAAI/bge-small-en-v1.5 by default) for 384-dim embeddings. Functions: `embed_text()`, `embed_texts()`, `embed_profile()`, `store_embedding()`, `store_label()`, `get_all_unlabeled_embeddings()`, `get_unlabeled_profiles()`, `get_labeled_data()`, `count_labeled()`, `get_embedded_lead_ids()`, `get_qualification_reason()`, `ensure_embeddings_table()`.
- **`ml/qualifier.py:BayesianQualifier`** — Pipeline(PCA, StandardScaler, GaussianProcessRegressor) with lazy refit. PCA dimensions selected via LML cross-validation. `update(embedding, label)` appends to training data and invalidates fit. `predict(embedding)` returns `(prob, entropy, posterior_std)` 3-tuple or `None` if unfitted. `predicted_probs(embeddings)` returns probability array. `bald_scores(embeddings)` computes BALD via MC sampling from GP posterior. `rank_profiles(profiles, pipeline=None)` sorts by predicted probability (descending); optional `pipeline` kwarg for partner campaigns. `explain_profile(profile)` returns human-readable explanation. `warm_start(X, y)` bulk-loads historical labels and fits once. Fitted pipeline persisted via `joblib` to `MODEL_PATH`. Also exports `qualify_profile_llm(profile_text, product_docs, campaign_objective)` for LLM-based lead qualification with structured output (`QualificationDecision`).
- **`ml/profile_text.py`** — `build_profile_text()`: concatenates all text fields from profile dict (headline, summary, positions, educations, etc.), lowercased. Used for embedding input.
- **`ml/search_keywords.py`** — `generate_search_keywords(product_docs, campaign_objective, n_keywords=10, exclude_keywords=None)`: calls LLM via `search_keywords.j2` prompt to generate LinkedIn People search queries. `exclude_keywords` prevents regenerating already-used terms.
- **`ml/hub.py`** — Partner campaign hub. `get_kit()` downloads from HuggingFace (`eracle/campaign-kit`), loads `config.json` + `model.joblib`, returns `{"config": dict, "model": sklearn-compatible}` or `None`. `import_partner_campaign(kit_config)` creates/updates a partner `Campaign` with `is_partner=True`. Cached after first attempt.
- **`rate_limiter.py:RateLimiter`** — Daily/weekly rate limits with auto-reset. Supports external exhaustion (LinkedIn-side limits).
- **`sessions/account.py:AccountSession`** — Central session object. Loads `LinkedInProfile` from DB, exposes `linkedin_profile`, `campaign` (singular, set by daemon before each lane), `campaigns` (property, all campaigns via group membership), `django_user`, `account_cfg` dict, and Playwright browser. Passed throughout the codebase.
- **`db/crm_profiles.py`** — Profile CRUD backed by DjangoCRM models. Lead-level functions: `lead_exists()`, `create_enriched_lead()`, `disqualify_lead()`, `promote_lead_to_contact()`, `get_leads_for_qualification()`, `count_leads_for_qualification()`, `pipeline_needs_refill()`. Deal-level functions: `get_profile()`, `set_profile_state()`, `get_qualified_profiles()`, `get_pending_profiles()`, `get_connected_profiles()`, `save_chat_message()`. Partner: `seed_partner_deals()`. `get_profile()` derives state from Lead attributes when no Deal exists. `set_profile_state()` clears `next_step` on any transition to/from PENDING.
- **`gdpr.py`** — GDPR location detection for newsletter auto-subscription. Checks LinkedIn country code against a static set of ISO-2 codes for opt-in email marketing jurisdictions (EU/EEA, UK, Switzerland, Canada, Brazil, Australia, Japan, South Korea, New Zealand). Missing/None codes default to protected. `apply_gdpr_newsletter_override()` updates `LinkedInProfile.subscribe_newsletter` in DB for non-GDPR locations.
- **`onboarding.py`** — DB-backed onboarding. `ensure_onboarding()` ensures `LLM_API_KEY` + `AI_MODEL` in `.env`, Campaign in DB, and active LinkedInProfile in DB. If missing, prompts user interactively. Creates Django models directly.
- **`conf.py`** — Loads `LLM_API_KEY` from `.env`. Exports `CAMPAIGN_CONFIG` dict (timing and ML defaults as Python constants), `AI_MODEL`, `LLM_API_BASE`, path constants (`EMBEDDINGS_DB`, `MODEL_PATH`, `PROMPTS_DIR`, etc.). `get_first_active_profile_handle()` queries `LinkedInProfile` model.
- **`api/voyager.py`** — Parses LinkedIn's Voyager API JSON responses into clean dicts via internal dataclasses (`LinkedInProfile`, `Position`, `Education`). Uses URN reference resolution from the `included` array.
- **`django_settings.py`** — Django settings importing DjangoCRM's default settings. SQLite DB at `assets/data/crm.db`.
- **`management/setup_crm.py`** — Idempotent bootstrap: creates Department, Deal Stages, ClosingReasons, LeadSource, and default Campaign (with followup template seeded from hardcoded content).
- **`templates/renderer.py`** — `render_template(session, template_content, profile)` renders a Jinja2 template string via `env.from_string()`, then passes through LLM. Appends `booking_link` from campaign. Product description injected from `session.campaign.product_docs`.
- **`navigation/`** — Login flow, throttling, browser utilities. `utils.py` always extracts `/in/` profile URLs from pages visited (auto-discovery).
- **`actions/`** — Individual browser actions (scrape, connect, message, search).

### Configuration
- **`.env`** — `LLM_API_KEY` (required), `AI_MODEL` (required). Optionally `LLM_API_BASE`. All prompted during onboarding if missing.
- **`conf.py:CAMPAIGN_CONFIG`** — Hardcoded timing/ML defaults:
  - `check_pending_recheck_after_hours` (24), `enrich_min_interval` (1), `min_action_interval` (120)
  - `qualification_entropy_threshold` (0.3), `qualification_max_auto_std` (0.05), `qualification_min_auto_accept_prob` (0.9), `qualification_n_mc_samples` (100)
  - `embedding_model` ("BAAI/bge-small-en-v1.5"), `min_qualifiable_leads` (50)
- **Campaign model** — `product_docs`, `campaign_objective`, `followup_template`, `booking_link` — managed via Django Admin or onboarding.
- **LinkedInProfile model** — `linkedin_username`, `linkedin_password`, `subscribe_newsletter`, `active`, `connect_daily_limit` (20), `connect_weekly_limit` (100), `follow_up_daily_limit` (30) — managed via Django Admin or onboarding.
- **`assets/templates/prompts/qualify_lead.j2`** — Jinja2 prompt template for LLM-based lead qualification. Receives `product_docs`, `campaign_objective`, and `profile_text` variables.
- **`assets/templates/prompts/search_keywords.j2`** — Jinja2 prompt template for LLM-based search keyword generation. Receives `product_docs`, `campaign_objective`, and `n_keywords` variables.
- **`pyproject.toml`** — Canonical dependency list for local dev via `uv`. DjangoCRM's `mysqlclient` dependency excluded via `[tool.uv] override-dependencies`. Dev deps (pytest, factory-boy) in `[dependency-groups] dev`.
- **`requirements/`** — `crm.txt` (DjangoCRM), `base.txt` (runtime deps, includes `analytics.txt`), `analytics.txt` (dbt-core + dbt-duckdb), `local.txt` (adds pytest/factory-boy), `production.txt`. Used by Docker only; must stay in sync with `pyproject.toml`.

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
Managed via `pyproject.toml` (local dev with `uv`) and `requirements/` (Docker). DjangoCRM's `mysqlclient` is excluded via `[tool.uv] override-dependencies` locally and `--no-deps` in Docker.

Core: `playwright`, `playwright-stealth`, `Django`, `django-crm-admin`, `pandas`, `langchain`/`langchain-openai`, `jinja2`, `pydantic`, `jsonpath-ng`, `tendo`, `termcolor`
ML/Embeddings: `scikit-learn` (GaussianProcessRegressor), `numpy`, `duckdb`, `fastembed`, `joblib`
Analytics: `dbt-core` 1.11.x, `dbt-duckdb` 1.10.x, `protobuf` 6.33.x (6.32.x had memory regression, resolved in 6.33+)
