# Architecture

Detailed module documentation for OpenOutreach. See `CLAUDE.md` for rules and quick reference.

## Entry Flow

`manage.py` (Django bootstrap + auto-migrate + CRM setup):
- Suppresses Pydantic serialization warning from langchain-openai. Configures logging: DEBUG level, suppresses noisy third-party loggers.
- No args → runs daemon: `ensure_onboarding()` → validate `LLM_API_KEY` → `get_or_create_session()` → `ensure_browser()` → `ensure_self_profile()` → GDPR newsletter override → `run_daemon(session)`.
- Any args → delegates to Django's `execute_from_command_line`.

## Onboarding (`onboarding.py`)

`ensure_onboarding()` ensures Campaign, active LinkedInProfile, LLM config, and legal acceptance exist. Four checks:

1. **Campaign** — interactive prompts for campaign name, product docs, objective, booking link. Creates `Department` + `Campaign`.
2. **LinkedInProfile** — prompts for LinkedIn email, password, newsletter, rate limits. Handle from email slug.
3. **LLM config** — prompts for `LLM_API_KEY`, `AI_MODEL`, `LLM_API_BASE` → writes to `.env`.
4. **Legal notice** — per-account acceptance stored as `LinkedInProfile.legal_accepted`.

## Profile State Machine

`enums.py:ProfileState` (TextChoices) values ARE CRM stage names: QUALIFIED, READY_TO_CONNECT, PENDING, CONNECTED, COMPLETED, FAILED. Pre-Deal states: url_only (no description), enriched (has description). `Lead.disqualified=True` = permanent account-level exclusion. LLM rejections = FAILED Deals with "Disqualified" closing reason (campaign-scoped).

## Task Queue

Persistent queue backed by `Task` model. Worker loop in `daemon.py`: pop oldest due task → RUNNING → dispatch → COMPLETED/FAILED. `heal_tasks()` reconciles on startup.

Three task types (handlers in `linkedin/tasks/`, signature: `handle_*(task, session, qualifiers)`):

1. **`handle_connect`** — Unified via `ConnectStrategy` dataclass. Regular: `find_candidate()` from `pools.py`; freemium: `find_freemium_candidate()`. Unreachable detection after `MAX_CONNECT_ATTEMPTS` (3).
2. **`handle_check_pending`** — Per-profile. Exponential backoff with jitter. On acceptance → enqueues `follow_up`.
3. **`handle_follow_up`** — Per-profile. Runs agentic follow-up via `run_follow_up_agent()`. Safety net re-enqueues in 72h.

## Qualification ML Pipeline

GPR (sklearn, ConstantKernel * RBF) inside Pipeline(StandardScaler, GPR) with BALD active learning:

1. **Balance-driven selection** — n_negatives > n_positives → exploit (highest P); otherwise → explore (highest BALD).
2. **LLM decision** — All decisions via LLM (`qualify_lead.j2`). GP only for candidate selection and confidence gate.
3. **READY_TO_CONNECT gate** — P(f > 0.5) above `min_ready_to_connect_prob` (0.9) promotes QUALIFIED → READY_TO_CONNECT.

384-dim FastEmbed embeddings, per-campaign models at `assets/models/campaign_{id}_model.joblib`. Cold start returns None until ≥2 labels of both classes.

## CRM Data Model

- **Campaign** — 1:1 with Department. `product_docs`, `campaign_objective`, `booking_link`, `is_freemium`, `action_fraction`.
- **LinkedInProfile** — 1:1 with User. Credentials, rate limits. Methods: `can_execute`/`record_action`/`mark_exhausted`.
- **SearchKeyword** — FK to Campaign. `keyword`, `used`, `used_at`. Unique on `(campaign, keyword)`.
- **ActionLog** — FK to LinkedInProfile + Campaign. `action_type`, `created_at`. Composite index.
- **Lead** — Per LinkedIn URL. `description` = parsed profile JSON. `disqualified` = permanent exclusion.
- **Contact** — Created after qualification (promotion from Lead).
- **Company** — From first position's company name.
- **Deal** — Per campaign (department-scoped). Stage maps to ProfileState. `next_step` = JSON metadata.
- **ProfileEmbedding** — 384-dim vectors as BinaryField. `lead_id` PK. Labels derived from Deal stage/closing_reason.
- **Task** — `task_type`, `status`, `scheduled_at`, `payload` (JSONField). Composite index on `(status, scheduled_at)`.
- **TheFile** — Raw Voyager JSON via GenericForeignKey.

## Key Modules

- **`daemon.py`** — Worker loop, `_build_qualifiers()`, `heal_tasks()`, freemium import.
- **`tasks/connect.py`** — `handle_connect`, `ConnectStrategy`, enqueue helpers.
- **`tasks/check_pending.py`** — `handle_check_pending`, exponential backoff.
- **`tasks/follow_up.py`** — `handle_follow_up`, rate limiting.
- **`pipeline/qualify.py`** — `run_qualification()`, `fetch_qualification_candidates()`.
- **`pipeline/search.py`** — `run_search()`, keyword management.
- **`pipeline/search_keywords.py`** — `generate_search_keywords()` via LLM.
- **`pipeline/ready_pool.py`** — GP confidence gate, `promote_to_ready()`.
- **`pipeline/pools.py`** — Composable generators: `search_source` → `qualify_source` → `ready_source`.
- **`pipeline/freemium_pool.py`** — Seed priority + undiscovered pool, ranked by qualifier.
- **`ml/qualifier.py`** — `Qualifier` protocol, `BayesianQualifier`, `KitQualifier`, `qualify_with_llm()`.
- **`ml/embeddings.py`** — FastEmbed utilities, `embed_profile()`.
- **`ml/profile_text.py`** — `build_profile_text()`.
- **`ml/hub.py`** — HuggingFace kit loader.
- **`browser/session.py`** — `AccountSession` (central session object).
- **`browser/registry.py`** — `AccountSessionRegistry`, `get_or_create_session()`.
- **`browser/login.py`** — Browser launch + LinkedIn login.
- **`browser/nav.py`** — Navigation, auto-discovery, `goto_page()`.
- **`db/leads.py`** — Lead CRUD, `lead_to_profile_dict()`, `get_leads_for_qualification()`.
- **`db/deals.py`** — Deal/state ops, `set_profile_state()`, `create_disqualified_deal()`.
- **`db/enrichment.py`** — Lazy enrichment/embedding.
- **`db/chat.py`** — `save_chat_message()`.
- **`conf.py`** — Config loading, `CAMPAIGN_CONFIG`, path constants.
- **`onboarding.py`** — Interactive setup.
- **`agents/follow_up.py`** — ReAct agent for follow-up conversations.
- **`actions/`** — `connect.py`, `status.py`, `message.py`, `profile.py`, `search.py`, `conversations.py`.
- **`api/voyager.py`** — Voyager API response parsing.
- **`api/messaging/`** — Messaging API (send, fetch conversations/messages).
- **`setup/freemium.py`** — Freemium campaign import + seed profiles.
- **`setup/gdpr.py`** — GDPR newsletter override.
- **`setup/self_profile.py`** — Self-profile sentinel.
- **`management/setup_crm.py`** — Idempotent CRM bootstrap (stages, closing reasons).
- **`admin.py`** — Django Admin registrations.
- **`django_settings.py`** — Django settings (SQLite at `assets/data/crm.db`).

## Configuration

- **`.env`** — `LLM_API_KEY` (required), `AI_MODEL` (required), `LLM_API_BASE` (optional).
- **`conf.py:CAMPAIGN_CONFIG`** — `min_ready_to_connect_prob` (0.9), `min_positive_pool_prob` (0.20), `connect_delay_seconds` (10), `connect_no_candidate_delay_seconds` (300), `check_pending_recheck_after_hours` (24), `check_pending_jitter_factor` (0.2), `worker_poll_seconds` (5), `qualification_n_mc_samples` (100), `embedding_model` ("BAAI/bge-small-en-v1.5").
- **Prompt templates** — `qualify_lead.j2` (temp 0.7), `search_keywords.j2` (temp 0.9), `follow_up_agent.j2`.
- **`requirements/`** — `crm.txt`, `base.txt`, `local.txt`, `production.txt`.

## Docker

Base image: `mcr.microsoft.com/playwright/python:v1.55.0-noble`. VNC on port 5900. `BUILD_ENV` arg selects requirements. Install: uv pip → DjangoCRM `--no-deps` → requirements → Playwright chromium.

## CI/CD

- `tests.yml` — pytest in Docker on push to `master` and PRs.
- `deploy.yml` — Tests → build + push to `ghcr.io/eracle/openoutreach`. Tags: `latest`, `sha-<commit>`, semver.

## Dependencies

`requirements/` files. DjangoCRM's `mysqlclient` excluded via `--no-deps`. `uv pip install` for fast installs.

Core: `playwright`, `playwright-stealth`, `Django`, `django-crm-admin`, `pandas`, `langchain`/`langchain-openai`, `jinja2`, `pydantic`, `jsonpath-ng`, `tendo`, `termcolor`
ML: `scikit-learn`, `numpy`, `fastembed`, `joblib`
