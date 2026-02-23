# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Documentation Rule

When modifying code, always update CLAUDE.md and MEMORY.md to reflect the changes. This includes changes to models, function signatures, module structure, configuration keys, state machines, lane behavior, ML pipeline, and any other architectural details documented in these files. Documentation must stay in sync with the code at all times.

## Commit Rule

Do not add `Co-Authored-By` lines to commit messages.

## Dependency Rule

Dependencies are managed in `requirements/*.txt` files. `requirements/` files are used by both local dev and Docker.

## Project Overview

OpenOutreach is a self-hosted LinkedIn automation tool for B2B lead generation. It uses Playwright with stealth plugins for browser automation and LinkedIn's internal Voyager API for structured profile data. The CRM backend is powered by DjangoCRM with Django Admin UI.

## Commands

### Docker (Recommended)
```bash
docker run --pull always -it -p 5900:5900 --user "$(id -u):$(id -g)" -v ./assets:/app/assets ghcr.io/eracle/openoutreach:latest  # run from pre-built image
make build    # build Docker images
make up       # build from source + run
make stop     # stop services
make attach   # follow logs
make up-view  # run + open VNC viewer
make view     # open VNC viewer (vinagre)
```

### Local Development
```bash
make setup                           # install deps + Playwright browsers + migrate + bootstrap CRM
make run                             # run the daemon (interactive onboarding on first run)
make admin                           # Django Admin at http://localhost:8000/admin/
make analytics                       # build dbt models (DuckDB analytics)
make analytics-test                  # run dbt schema tests
python manage.py migrate             # run Django migrations
python manage.py createsuperuser     # create Django admin user
```

### Testing
```bash
make test                         # run tests locally
make docker-test                  # run tests in Docker
pytest tests/api/test_voyager.py  # run single test file
pytest -k test_name               # run single test by name
```

## Architecture

### Entry Flow
`manage.py` (Django bootstrap + auto-migrate + CRM setup):
- Suppresses Pydantic serialization warning from langchain-openai. Configures logging: DEBUG level, suppresses noisy third-party loggers (urllib3, httpx, langchain, openai, dbt, playwright, httpcore, fastembed, huggingface_hub, filelock).
- No args → runs the daemon: `ensure_onboarding()` → validate `LLM_API_KEY` → get session (sets default campaign: first non-partner or first available) → `ensure_browser()` → `ensure_self_profile()` (creates disqualified Lead + `/in/me/` sentinel via Voyager API) → GDPR newsletter override (guarded by marker file `.{handle}_newsletter_processed`) → `run_daemon(session)` which initializes the `BayesianQualifier` (GPR pipeline, warm-started from historical labels) and spreads actions at a configurable pace across multiple campaigns. New profiles are auto-discovered as the daemon navigates LinkedIn pages. When all lanes are idle, LLM-generated search keywords discover new profiles.
- Any args → delegates to Django's `execute_from_command_line` (e.g. `runserver`, `migrate`, `createsuperuser`).

### Onboarding (`onboarding.py`)
Before the daemon starts, `ensure_onboarding()` ensures a Campaign, active LinkedInProfile, LLM config, and legal acceptance exist. Four checks:

1. **Campaign** — if no `Campaign` exists in DB, runs interactive prompts for campaign name, product docs, campaign objective, booking link. Creates `Department` + `Campaign` (followup template seeded from `followup2.j2`).
2. **LinkedInProfile** — if no active profile exists, prompts for LinkedIn email, password, newsletter preference, and rate limits. Creates `User` + `LinkedInProfile`. Handle derived from email slug (part before `@`, lowercased, dots/plus → underscores). User created with `is_staff=True` and unusable password.
3. **LLM config** — if missing from `.env`, prompts user for `LLM_API_KEY` (required), `AI_MODEL` (required), and `LLM_API_BASE` (optional), and writes them to `.env`.
4. **Legal notice** — `_require_legal_acceptance()` displays GitHub URL to `LEGAL_NOTICE.md`, prompts for acceptance (y/n). Guarded by marker file at `COOKIES_DIR/.legal_notice_accepted` — only runs once.

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

The pipeline uses PCA (dimension selected via LML cross-validation over candidates `{2,4,6,10,15,20}`, capped at `min(n-1, 384)`) + StandardScaler + GPR on 384-dim FastEmbed embeddings. GPR kernel: `ConstantKernel(1.0) * RBF(length_scale=sqrt(n_pca))` with `alpha=0.1` and `n_restarts_optimizer=3`. Training data is accumulated incrementally; the model is lazily re-fitted (on ALL data, O(n³)) whenever predictions are requested after new labels arrive. The fitted pipeline is persisted via `joblib` to `MODEL_PATH` (atomic write via tmp+rename). On daemon restart, `warm_start()` bulk-loads historical labels and fits once.

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
- **`daemon.py`** — Main daemon loop. `LaneSchedule` class tracks `next_run` per major lane; `reschedule()` adds ±20% jitter. `_PromoRotator` logs rotating promotional messages every N ticks. `_rebuild_analytics()` runs `dbt run` with 120s timeout. `run_daemon(session)` creates `BayesianQualifier` (GPR pipeline, warm-started from historical labels), rate limiters (from `LinkedInProfile` model, shared across campaigns), `LaneSchedule` objects per campaign for three major lanes, qualify lane for gap-filling, and a search lane as lowest-priority gap-filler. Supports multi-campaign architecture: iterates `session.campaigns`, creates separate schedules per campaign, with shared qualifier/rate limiters. Partner campaigns use probabilistic gating (`action_fraction`); regular campaigns inversely gated with probability = `max(partner.action_fraction)`. Initializes embeddings table at startup. Also imports partner campaigns via `ml/hub.py`.
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
- **`sessions/account.py:AccountSession`** — Central session object. Loads `LinkedInProfile` from DB, exposes `linkedin_profile`, `campaign` (singular, set by daemon before each lane), `campaigns` (property, all campaigns via group membership), `django_user`, `account_cfg` dict (handle, username, password, subscribe_newsletter, active, cookie_file), and Playwright browser (`page`, `context`, `browser`, `playwright`). Key methods: `ensure_browser()` (launches/recovers browser + login), `wait()` (human delay + page load), `_maybe_refresh_cookies()` (re-login if `li_at` cookie expired), `close()` (graceful teardown). Passed throughout the codebase.
- **`sessions/registry.py:AccountSessionRegistry`** — Singleton registry for `AccountSession` instances. `get_or_create(handle)` normalizes handle (lowercase + strip) and reuses existing sessions. `close_all()` tears down all sessions. Public convenience function: `get_session(handle)` wraps `AccountSessionRegistry.get_or_create()`.
- **`db/crm_profiles.py`** — Profile CRUD backed by DjangoCRM models. Lead-level functions: `lead_exists()`, `create_enriched_lead()`, `disqualify_lead()`, `promote_lead_to_contact()`, `get_leads_for_qualification()`, `count_leads_for_qualification()`, `pipeline_needs_refill()`. Deal-level functions: `get_profile()`, `set_profile_state()`, `get_qualified_profiles()`, `count_qualified_profiles()`, `get_pending_profiles()`, `get_connected_profiles()`, `get_updated_at_map()`, `save_chat_message()`. URL helpers: `url_to_public_id(url)` (strict extractor, path must start with `/in/`), `public_id_to_url(public_id)`. Partner: `seed_partner_deals()`. Private helpers: `_make_ticket()` (uuid4 hex[:16]), `_update_lead_fields()`, `_ensure_company()`, `_attach_raw_data()`. `get_profile()` derives state from Lead attributes when no Deal exists. `set_profile_state()` clears `next_step` on any transition to/from PENDING.
- **`gdpr.py`** — GDPR location detection for newsletter auto-subscription. Checks LinkedIn country code against a static set of ISO-2 codes for opt-in email marketing jurisdictions (EU/EEA, UK, Switzerland, Canada, Brazil, Australia, Japan, South Korea, New Zealand). Missing/None codes default to protected. `apply_gdpr_newsletter_override()` updates `LinkedInProfile.subscribe_newsletter` in DB for non-GDPR locations.
- **`onboarding.py`** — DB-backed onboarding. `ensure_onboarding()` ensures `LLM_API_KEY` + `AI_MODEL` in `.env`, Campaign in DB, and active LinkedInProfile in DB. If missing, prompts user interactively. Creates Django models directly.
- **`conf.py`** — Loads `LLM_API_KEY` from `.env`. `load_dotenv()` checks `assets/.env` first (Docker volume, persists across recreations), then project root for backwards compat. `ENV_FILE = ASSETS_DIR / ".env"` (writes go to `assets/.env`). Exports `CAMPAIGN_CONFIG` dict (timing and ML defaults as Python constants), `AI_MODEL`, `LLM_API_BASE`, path constants (`EMBEDDINGS_DB`, `MODEL_PATH`, `PROMPTS_DIR`, `DEFAULT_FOLLOWUP_TEMPLATE_PATH`, etc.). `PARTNER_LOG_LEVEL = logging.DEBUG` (suppresses partner campaign messages at normal verbosity). `MIN_DELAY`/`MAX_DELAY` (5/8s) for human-like wait timing. `get_first_active_profile_handle()` queries `LinkedInProfile` model. Creates `COOKIES_DIR`, `DATA_DIR`, `MODELS_DIR` on import.
- **`api/voyager.py`** — Parses LinkedIn's Voyager API JSON responses into clean dicts via internal dataclasses (`LinkedInProfile`, `Position`, `Education`). Uses URN reference resolution from the `included` array.
- **`django_settings.py`** — Django settings importing DjangoCRM's default settings. SQLite DB at `assets/data/crm.db`. Key settings: `SECRET_KEY` = hardcoded dev key, `DEBUG = True`, `ALLOWED_HOSTS = ["*"]`, `SITE_ID = 1`, `SITE_TITLE = "OpenOutreach CRM"`, `ADMIN_HEADER = "OpenOutreach Admin"`, `MEDIA_ROOT = DATA_DIR / "media"` (Path, not str), `DJANGO_ALLOW_ASYNC_UNSAFE = "true"`. INSTALLED_APPS includes all DjangoCRM apps + `linkedin`.
- **`admin.py`** — Django Admin registrations: `CampaignAdmin` (list_display: department, booking_link, is_partner, action_fraction), `LinkedInProfileAdmin` (list_display: user, username, active; list_filter: active), `SearchKeywordAdmin` (list_display: keyword, campaign, used, used_at; list_filter: used, campaign). All use `raw_id_fields` for FK/O2O fields.
- **`management/setup_crm.py`** — Idempotent bootstrap. `setup_crm()` creates Site, "co-workers" Group, Department (`DEPARTMENT_NAME = "LinkedIn Outreach"`). `ensure_campaign_pipeline(dept)` creates 5 stages (New, Pending, Connected, Completed, Failed), 2 closing reasons (Completed=success, Failed), and "LinkedIn Scraper" LeadSource. `_check_legacy_stages(dept)` aborts if DB has deals at invalid stages.
- **`templates/renderer.py`** — Two-stage template rendering. `call_llm(prompt)` creates `ChatOpenAI` with `temperature=0.7` and `AI_MODEL`. `render_template(session, template_content, profile)` first renders Jinja2 template (with profile context + `product_description` from `session.campaign.product_docs`), then passes result through `call_llm()`, then appends `booking_link` from campaign (after LLM call, not part of prompt).
- **`navigation/login.py`** — Playwright browser setup and LinkedIn login. `build_playwright()` creates a fresh browser instance. `init_playwright_session(session, handle)` loads saved cookies or performs fresh login. `playwright_login(session)` performs email/password login with human-like typing.
- **`navigation/utils.py`** — Browser navigation utilities. `goto_page(session, action, expected_url_pattern)` navigates and auto-discovers `/in/` URLs via `_extract_in_urls()`. `_enrich_new_urls(session, urls)` auto-enriches discovered profiles (Voyager API + create Lead + embed), rate-limited by `enrich_min_interval` (1s). `human_type(locator, text)` types with random per-keystroke delay (50-200ms). `get_top_card(session)` finds profile card with fallback selectors (`TOP_CARD_SELECTORS`). `first_matching(page, selectors)` returns first visible locator. `save_page(session, profile)` saves HTML to fixtures.
- **`navigation/exceptions.py`** — Custom exceptions: `AuthenticationError`, `TerminalStateError`, `SkipProfile`, `ReachedConnectionLimit`.
- **`actions/connection_status.py`** — `get_connection_status(session, profile) → ProfileState`: fast path via `connection_degree == 1` (trusted), fallback to UI text/button inspection. Text priority: "Pending" → PENDING, "1st"/"1st degree" → CONNECTED, "Connect" button → NEW. Has CLI `__main__` block.
- **`actions/connect.py`** — `send_connection_request(session, profile) → ProfileState`: tries `_connect_direct()` (top card button), falls back to `_connect_via_more()` (More menu). Sends WITHOUT a note. `_check_weekly_invitation_limit(session)` raises `ReachedConnectionLimit` on limit popup. Has CLI `__main__` block.
- **`actions/message.py`** — `send_follow_up_message(session, profile) → str | None`: checks connection status first, renders template via `render_template()`, sends via popup (`_send_msg_pop_up`) or direct messaging thread (`_send_message`). Returns message text or None. Has CLI `__main__` block.
- **`actions/profile.py`** — `scrape_profile(session, profile) → (profile_dict, raw_data)`: calls Voyager API via `PlaywrightLinkedinAPI`. Has CLI `__main__` block with `--save-fixture` flag.
- **`actions/search.py`** — `search_profile(session, profile)`: direct URL navigation (no human search simulation). `search_people(session, keyword, page=1)`: LinkedIn People search with pagination. Auto-discovery via `goto_page()`. Has CLI `__main__` block.

### Configuration
- **`.env`** — `LLM_API_KEY` (required), `AI_MODEL` (required). Optionally `LLM_API_BASE`. All prompted during onboarding if missing.
- **`conf.py:CAMPAIGN_CONFIG`** — Hardcoded timing/ML defaults:
  - `check_pending_recheck_after_hours` (24), `enrich_min_interval` (1), `min_action_interval` (120)
  - `qualification_entropy_threshold` (0.3), `qualification_max_auto_std` (0.05), `qualification_min_auto_accept_prob` (0.9), `qualification_n_mc_samples` (100)
  - `embedding_model` ("BAAI/bge-small-en-v1.5"), `min_qualifiable_leads` (50)
- **Campaign model** — `product_docs`, `campaign_objective`, `followup_template`, `booking_link` — managed via Django Admin or onboarding.
- **LinkedInProfile model** — `linkedin_username`, `linkedin_password`, `subscribe_newsletter`, `active`, `connect_daily_limit` (20), `connect_weekly_limit` (100), `follow_up_daily_limit` (30) — managed via Django Admin or onboarding.
- **`assets/templates/prompts/qualify_lead.j2`** — LLM-based lead qualification. Receives `product_docs`, `campaign_objective`, `profile_text`. Structured output: `QualificationDecision(qualified: bool, reason: str)`. LLM temperature: **0.7**, timeout: 60s.
- **`assets/templates/prompts/search_keywords.j2`** — LLM-based search keyword generation. Receives `product_docs`, `campaign_objective`, `n_keywords`, `exclude_keywords`. Structured output: `SearchKeywords(keywords: list[str])`. LLM temperature: **0.9** (high diversity).
- **`assets/templates/prompts/followup2.j2`** — Follow-up message template. Receives `full_name`, `headline`, `current_company`, `location`, `product_description`, `shared_connections`. Constraints: 2-4 sentences, max 400 chars, NO placeholders, warm tone, soft CTA.
- **`requirements/`** — `crm.txt` (DjangoCRM, installed with `--no-deps`), `base.txt` (runtime deps, includes `analytics.txt`), `analytics.txt` (dbt-core + dbt-duckdb), `local.txt` (adds pytest/factory-boy), `production.txt`. Used by both local dev and Docker.

### Analytics Layer (dbt + DuckDB)
The `analytics/` directory contains a dbt project that reads from the CRM SQLite DB (via DuckDB's SQLite attach) to build ML training sets. No CRM data is modified.

- **`analytics/profiles.yml`** — DuckDB profile config. Attaches `assets/data/crm.db` as `crm`. Memory limit set to 2GB.
- **`analytics/models/staging/`** — Staging views over CRM tables (`stg_leads`, `stg_deals`, `stg_stages`). Lead JSON fields (including `industry_name`, `geo_name`) are parsed here.
- **`analytics/models/marts/ml_connection_accepted.sql`** — Binary classification training set: did a connection request get accepted? Target=1 (reached CONNECTED/COMPLETED), Target=0 (stuck at PENDING). Excludes DISCOVERED/ENRICHED/FAILED profiles. Uses LATERAL UNNEST CTEs to extract 24 mechanical features from positions/educations JSON arrays, plus a concatenated `profile_text` column for keyword feature extraction in Python.
- **Output:** `assets/data/analytics.duckdb` — query with `duckdb.connect("assets/data/analytics.duckdb")`.
- **Deps:** `dbt-core 1.11.x` + `dbt-duckdb 1.10.x` + `protobuf` 6.33.x (pinned; earlier 6.32.x had a memory regression ~5GB RSS on startup, resolved in 6.33+).

### Error Handling Convention
The application should crash on unexpected errors. `try/except` blocks should only handle expected, recoverable errors. Custom exceptions in `navigation/exceptions.py`: `AuthenticationError`, `TerminalStateError`, `SkipProfile`, `ReachedConnectionLimit`.

### Docker
- **Base image:** `mcr.microsoft.com/playwright/python:v1.55.0-noble` (includes browsers + system deps).
- **VNC access:** Xvfb (virtual display) + x11vnc on port 5900 for remote viewing.
- **Build arg:** `BUILD_ENV` (default: production) selects requirements file.
- **Install order:** uv pip → DjangoCRM via `--no-deps` → requirements → Playwright chromium.
- **Startup:** `/start` script (CRLF normalized with sed).

### CI/CD
- **`.github/workflows/tests.yml`** — Runs pytest (in Docker) and dbt tests (Python 3.12, ubuntu-latest) on push to `master` and PRs.
- **`.github/workflows/deploy.yml`** — On push to `master` or version tags (`v*`): runs tests, then builds and pushes the production Docker image to `ghcr.io/eracle/openoutreach`. Tags: `latest`, `sha-<commit>`, semver (`v1.0.0` → `1.0.0` + `1.0`).

### Dependencies
Managed via `requirements/` files. DjangoCRM's `mysqlclient` is excluded via `--no-deps` in the install step. `uv pip install` is used for fast installs (both locally via `make setup` and in Docker).

Core: `playwright`, `playwright-stealth`, `Django`, `django-crm-admin`, `pandas`, `langchain`/`langchain-openai`, `jinja2`, `pydantic`, `jsonpath-ng`, `tendo`, `termcolor`
ML/Embeddings: `scikit-learn` (GaussianProcessRegressor), `numpy`, `duckdb`, `fastembed`, `joblib`
Analytics: `dbt-core` 1.11.x, `dbt-duckdb` 1.10.x, `protobuf` 6.33.x (6.32.x had memory regression, resolved in 6.33+)
