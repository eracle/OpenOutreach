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
python main.py                       # run with first active account
python main.py <handle>              # run with specific account
python manage_crm.py runserver       # Django Admin at http://localhost:8000/admin/
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
`main.py` (Django bootstrap) → `csv_launcher.launch_connect_follow_up_campaign()` → `campaigns.engine.start_campaign()` → `campaigns.connect_follow_up.process_profiles()`

### Profile State Machine
Each profile progresses through states defined in `navigation/enums.py:ProfileState`:
`DISCOVERED` → `ENRICHED` → `PENDING` → `CONNECTED` → `COMPLETED` (or `FAILED`)

States map to DjangoCRM Deal Stages (defined in `db/crm_profiles.py:STATE_TO_STAGE`).

The campaign engine (`campaigns/connect_follow_up.py`) uses `match/case` on the current state to determine the next action: scrape → connect → check status → send follow-up message.

### CRM Data Model
- **Lead** — Created per LinkedIn profile URL. Stores `first_name`, `last_name`, `title`, `website` (LinkedIn URL), `description` (full parsed profile JSON).
- **Contact** — Created after enrichment, linked to Company.
- **Company** — Created from first position's company name.
- **Deal** — Tracks pipeline stage. One Deal per Lead. Stage maps to ProfileState.
- **TheFile** — Raw Voyager API JSON attached to Lead via GenericForeignKey.

### Key Modules
- **`sessions/account.py:AccountSession`** — Central session object holding Playwright browser, Django User, and account config. Passed throughout the codebase.
- **`db/crm_profiles.py`** — Profile CRUD backed by DjangoCRM models. Same public API as the old SQLAlchemy layer. Contains `ProfileRow` wrapper for campaign compatibility.
- **`conf.py`** — Loads config from `.env` and `assets/accounts.secrets.yaml`. All paths derived from `ASSETS_DIR`.
- **`api/voyager.py`** — Parses LinkedIn's Voyager API JSON responses into clean dicts via internal dataclasses (`LinkedInProfile`, `Position`, `Education`). Uses URN reference resolution from the `included` array.
- **`django_settings.py`** — Django settings importing DjangoCRM's default settings. SQLite DB at `assets/data/crm.db`.
- **`management/setup_crm.py`** — Idempotent bootstrap: creates Department, Users, Deal Stages, ClosingReasons, LeadSource.
- **`templates/renderer.py`** — Jinja2 or AI-prompt-based message rendering. Template type (`jinja` or `ai_prompt`) configured per account. AI calls go through LangChain/OpenAI.
- **`navigation/`** — Login flow, throttling, and browser utilities.
- **`actions/`** — Individual browser actions (scrape, connect, message, search).

### Configuration
- **`assets/accounts.secrets.yaml`** — Account credentials, input CSV path, template path, and template type per account. Copy from `assets/accounts.secrets.template.yaml`.
- **`.env`** — `OPENAI_API_KEY`, `OPENAI_API_BASE`, `AI_MODEL` (defaults to `gpt-4o-mini`).
- **`requirements/`** — `crm.txt` (DjangoCRM, installed with `--no-deps`), `base.txt` (runtime deps), `local.txt` (adds pytest/factory-boy), `production.txt`.

### Error Handling Convention
The application should crash on unexpected errors. `try/except` blocks should only handle expected, recoverable errors. Custom exceptions in `navigation/exceptions.py`: `TerminalStateError`, `SkipProfile`, `ReachedConnectionLimit`.

### Dependencies
Core: `playwright`, `playwright-stealth`, `Django`, `django-crm-admin` (installed via `--no-deps` to skip mysqlclient), `pandas`, `langchain`/`langchain-openai`, `jinja2`, `pydantic`, `jsonpath-ng`, `tendo`
