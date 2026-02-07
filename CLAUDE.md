# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OpenOutreach is a self-hosted LinkedIn automation tool for B2B lead generation. It uses Playwright with stealth plugins for browser automation and LinkedIn's internal Voyager API for structured profile data. Each LinkedIn account gets its own SQLite database.

## Commands

### Local Development
```bash
python -m venv venv && source venv/bin/activate
pip install uv && uv pip install -r requirements/local.txt
playwright install --with-deps chromium
python main.py                    # run with first active account
python main.py <handle>           # run with specific account
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
`main.py` → `csv_launcher.launch_connect_follow_up_campaign()` → `campaigns.engine.start_campaign()` → `campaigns.connect_follow_up.process_profiles()`

### Profile State Machine
Each profile progresses through states defined in `navigation/enums.py:ProfileState`:
`DISCOVERED` → `ENRICHED` → `PENDING` → `CONNECTED` → `COMPLETED` (or `FAILED`)

The campaign engine (`campaigns/connect_follow_up.py`) uses `match/case` on the current state to determine the next action: scrape → connect → check status → send follow-up message.

### Key Modules
- **`sessions/account.py:AccountSession`** — Central session object holding Playwright browser, DB session, and account config. Passed throughout the codebase.
- **`conf.py`** — Loads config from `.env` and `assets/accounts.secrets.yaml`. All paths derived from `ASSETS_DIR`.
- **`api/voyager.py`** — Parses LinkedIn's Voyager API JSON responses into clean dicts via internal dataclasses (`LinkedInProfile`, `Position`, `Education`). Uses URN reference resolution from the `included` array.
- **`db/models.py`** — SQLAlchemy model. Single `Profile` table keyed on `public_identifier`, stores parsed profile JSON, raw API data, state, and sync status.
- **`templates/renderer.py`** — Jinja2 or AI-prompt-based message rendering. Template type (`jinja` or `ai_prompt`) configured per account. AI calls go through LangChain/OpenAI.
- **`navigation/`** — Login flow, throttling, and browser utilities.
- **`actions/`** — Individual browser actions (scrape, connect, message, search).

### Configuration
- **`assets/accounts.secrets.yaml`** — Account credentials, input CSV path, template path, and template type per account. Copy from `assets/accounts.secrets.template.yaml`.
- **`.env`** — `OPENAI_API_KEY`, `OPENAI_API_BASE`, `AI_MODEL` (defaults to `gpt-4o-mini`).
- **`requirements/`** — `base.txt` (runtime deps), `local.txt` (adds pytest/factory-boy), `production.txt`.

### Error Handling Convention
The application should crash on unexpected errors. `try/except` blocks should only handle expected, recoverable errors. Custom exceptions in `navigation/exceptions.py`: `TerminalStateError`, `SkipProfile`, `ReachedConnectionLimit`.

### Dependencies
Core: `playwright`, `playwright-stealth`, `SQLAlchemy`, `pandas`, `langchain`/`langchain-openai`, `jinja2`, `pydantic`, `jsonpath-ng`
