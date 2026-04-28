# CLAUDE.md

## Rules

- **Python env**: Always use `.venv/bin/python` (not system `python3`).
- **Commits**: No `Co-Authored-By` lines. Single-line messages (no body).
- **Dependencies**: Managed in `requirements/*.txt` (used by local dev and Docker).
- **Docs sync**: When modifying code, update CLAUDE.md and ARCHITECTURE.md to reflect changes.
- **No memory**: Never use the auto-memory system (no MEMORY.md, no memory files). All persistent context belongs in CLAUDE.md or ARCHITECTURE.md.
- **Error handling**: App should crash on unexpected errors. `try/except` only for expected, recoverable errors. Custom exceptions in `exceptions.py`.
- **No API backward compat**: Project has no external users yet — don't preserve old Python APIs, function signatures, or import paths. Rename, delete, and rewrite freely; no shims or re-export modules. DB schema changes still go through Django migrations as normal — existing installs must upgrade cleanly.

## Project Overview

OpenOutreach — self-hosted LinkedIn automation for B2B lead generation. Playwright + stealth for browser automation, LinkedIn Voyager API for profile data, Django + Django Admin for CRM (models owned by this project).

## Commands

```bash
# Docker
make build / make up / make stop / make logs / make up-view

# Local dev
make setup    # install deps + browsers + migrate + bootstrap CRM
make run      # run daemon
make admin    # Django Admin at localhost:8000/admin/

# Testing
make test / make docker-test
pytest tests/api/test_voyager.py   # single file
pytest -k test_name                # single test
```

## Architecture (quick reference)

For detailed module docs, see `ARCHITECTURE.md`.

- **Entry**: `manage.py` — stock Django management. `rundaemon` command (migrate → onboard → validate → task queue loop). `manage.py` with no args defaults to `rundaemon`. Onboarding logic in `onboarding.py`: `OnboardConfig` (pure dataclass), `missing_keys()`, `collect_from_wizard()`, single `apply()` write path. Docker `start` script handles Xvfb/VNC, then `exec python manage.py rundaemon`.
- **State machine**: `enums.py:ProfileState` — QUALIFIED → READY_TO_CONNECT → PENDING → CONNECTED → COMPLETED / FAILED. Deal.state is a CharField with ProfileState choices (no Stage model). `Outcome` (converted/not_interested/wrong_fit/no_budget/has_solution/bad_timing/unresponsive/unknown) on Deal.outcome. `Lead.disqualified=True` = permanent exclusion. LLM rejections = FAILED Deals with wrong_fit outcome (campaign-scoped).
- **Task queue**: `Task` model (persistent). Three types: `connect`, `check_pending`, `follow_up`. Handlers in `linkedin/tasks/`, signature: `handle_*(task, session, qualifiers)`. Task creation is centralized in `linkedin/tasks/scheduler.py` — no other module inserts Task rows. `set_profile_state()` fires `on_deal_state_entered(deal)`, which enqueues the task implied by the new state (CONNECTED → follow_up, PENDING → check_pending). The daemon calls `reconcile(session)` whenever the queue has no ready task: it recovers stale RUNNING rows, seeds one connect per campaign, and re-creates tasks for any active Deal without a pending task. This is the retry mechanism — a crashed handler leaves a FAILED task with no successor, and the next idle cycle re-creates it. On 401 (`AuthenticationError`), the daemon calls `session.reauthenticate()` and marks the task FAILED; reconcile picks it up.
- **ML pipeline**: GPR (sklearn) + BALD active learning + LLM qualification. Per-campaign models stored in `Campaign.model_blob` (DB).
- **Config**: `SiteConfig` DB singleton (LLM_PROVIDER, LLM_API_KEY, AI_MODEL, LLM_API_BASE — editable via Django Admin; `llm_provider` chooses between OpenAI/Anthropic/Google/Groq/Mistral/Cohere/openai_compatible, `llm_api_base` only consulted when provider is `openai_compatible`), `conf.py:CAMPAIGN_CONFIG` (timing/ML defaults), `conf.py` browser constants (`BROWSER_*`, `HUMAN_TYPE_*`), `conf.py` schedule constants (`ENABLE_ACTIVE_HOURS` flag, active hours/timezone/rest days), `conf.py` onboarding defaults (`DEFAULT_*_LIMIT`), `conf.py:FASTEMBED_CACHE_DIR` (persistent model cache, defaults to `<project>/.cache/fastembed/`), Campaign/LinkedInProfile models (Django Admin). `VOYAGER_REQUEST_TIMEOUT_MS` lives in `api/client.py` (constructor default on `PlaywrightLinkedinAPI`). `conf.py:DUMP_PAGES` (default `False`) — enable to save page HTML snapshots for fixture collection.
- **Lazy accessors**: `Lead.get_profile(session)` is a pure live Voyager scrape (no DB caching of the raw dict); `Lead.get_urn(session)` reads the `urn` column and falls back to a scrape; `Lead.get_embedding(session)` lazily scrapes + embeds on first access, then caches the 384-dim bytes on the row. `Lead.embed_from_profile(profile)` reuses an in-hand profile dict to skip the scrape (used by `create_enriched_lead`). `Lead.to_profile_dict()` returns a minimal `{lead_id, public_identifier, url, meta}` dict (no `profile` key). `AccountSession.campaigns` (cached_property, list). `AccountSession.self_profile` (cached_property, re-discovers via Voyager on first access per session — no DB cache).
- **Deal summaries**: `Deal.profile_summary` and `Deal.chat_summary` are lazy, mem0-style JSON fact lists built on demand and updated incrementally. `linkedin/db/summaries.py` is the single boundary — `materialize_profile_summary_if_missing(deal, session)` fires on the first follow-up touch (one Voyager re-scrape per `(lead, campaign)` lifetime), `update_chat_summary(deal, new_messages)` folds newly-synced ChatMessages into the summary via `reconcile_facts`, which routes new facts through mem0's UPDATE prompt to apply ADD/UPDATE/DELETE/NONE events (no naive append-and-dedup). **Only incoming (lead) messages reach fact extraction** — outgoing seller messages are filtered at the boundary so `chat_summary` stores facts about the lead, never the seller's pitch. A one-sided burst of outgoing messages short-circuits the LLM call entirely. The follow-up agent consumes `profile_summary + chat_summary + last 6 ChatMessage rows` instead of flat profile fields. The fact-extraction prompt is vendored at `linkedin/db/summaries.py:_FACT_EXTRACTION_PROMPT`; mem0's `DEFAULT_UPDATE_MEMORY_PROMPT` and `get_update_memory_messages` are vendored under `linkedin/vendor/mem0/configs/prompts.py` (mirroring upstream paths so future syncs are a clean diff). No `mem0ai` runtime dependency — avoids qdrant/grpcio/sqlalchemy transitive bloat.
- **Django apps**: `linkedin` (main — Campaign with users M2M), `crm` (Lead with embedding/Deal), `chat` (ChatMessage).
- **Data dir**: `data/` holds persistent state (`db.sqlite3`). Docker users mount volumes at `/app/data`.
- **Docker**: Playwright base image, VNC on port 5900, `BUILD_ENV` arg selects requirements.
- **CI/CD**: `.github/workflows/tests.yml` (pytest), `deploy.yml` (build + push to ghcr.io).
