# CLAUDE.md

## Rules

- **Python env**: Always use `.venv/bin/python` (not system `python3`).
- **Commits**: No `Co-Authored-By` lines. Single-line messages (no body).
- **Dependencies**: Managed in `requirements/*.txt` (used by local dev and Docker).
- **Docs sync**: When modifying code, update CLAUDE.md and MEMORY.md to reflect changes.
- **Error handling**: App should crash on unexpected errors. `try/except` only for expected, recoverable errors. Custom exceptions in `exceptions.py`.
- **No backward compat**: CRM models are owned by this project — no need for backward compatibility shims, legacy migration code, or re-export modules. Simplify freely.

## Project Overview

OpenOutreach — self-hosted LinkedIn automation for B2B lead generation. Playwright + stealth for browser automation, LinkedIn Voyager API for profile data, DjangoCRM + Django Admin for CRM.

## Commands

```bash
# Docker
make build / make up / make stop / make attach / make up-view

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

- **Entry**: `manage.py` — no args runs daemon (onboarding → browser → task queue loop); with args delegates to Django CLI.
- **State machine**: `enums.py:ProfileState` — QUALIFIED → READY_TO_CONNECT → PENDING → CONNECTED → COMPLETED / FAILED. `Lead.disqualified=True` = permanent exclusion. LLM rejections = FAILED Deals (campaign-scoped).
- **Task queue**: `Task` model (persistent). Three types: `connect`, `check_pending`, `follow_up`. Handlers in `linkedin/tasks/`, signature: `handle_*(task, session, qualifiers)`.
- **ML pipeline**: GPR (sklearn) + BALD active learning + LLM qualification. Per-campaign models at `assets/models/campaign_{id}_model.joblib`.
- **Config**: `.env` (LLM_API_KEY, AI_MODEL), `conf.py:CAMPAIGN_CONFIG` (timing/ML defaults), Campaign/LinkedInProfile models (Django Admin).
- **Docker**: Playwright base image, VNC on port 5900, `BUILD_ENV` arg selects requirements.
- **CI/CD**: `.github/workflows/tests.yml` (pytest), `deploy.yml` (build + push to ghcr.io).
