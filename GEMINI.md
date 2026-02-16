# GEMINI.md

## Project Overview

OpenOutreach is a self-hosted LinkedIn automation tool for B2B lead generation. It uses Playwright with stealth
plugins for browser automation and LinkedIn's internal Voyager API for structured profile data. The CRM backend
is powered by DjangoCRM with Django Admin UI.

Core functionalities:

* **Browser Automation**: Playwright with stealth plugins for human-like, undetectable interactions.
* **Daemon-Driven Workflow**: Four action lanes (enrich, connect, check_pending, follow_up) priority-scheduled at a fixed pace within configurable working hours.
* **ML-Driven Prioritization**: HistGradientBoostingClassifier + Thompson Sampling ranks profiles by predicted connection acceptance.
* **Built-in CRM**: DjangoCRM with Django Admin UI — Leads, Contacts, Companies, Deals tracked in a local SQLite database.
* **AI-Powered Messaging**: Jinja2 or LLM-generated templates for personalized follow-up messages (any OpenAI-compatible provider).
* **Analytics**: dbt + DuckDB pipeline builds ML training sets from CRM data.

## Building and Running

The project is managed using Docker Compose and a `Makefile`.

* **Build the Docker containers**:
  ```bash
  make build
  ```
* **Start the application**:
  ```bash
  make up
  ```
* **Stop the application**:
  ```bash
  make stop
  ```
* **View logs**:
  ```bash
  make attach
  ```
* **Run locally** (without Docker):
  ```bash
  make setup                    # install deps + migrate + bootstrap CRM
  playwright install --with-deps chromium
  make run                      # start the daemon
  make admin                    # Django Admin at http://localhost:8000/admin/
  ```

The `docker-compose.yml` file (`local.yml`) defines the `app` service that runs the LinkedIn automation.

To view the browser automation, use a VNC viewer to connect to `localhost:5900`.

## Development Conventions

### Dependencies

Python dependencies are managed in `requirements/`:
- `base.txt` — runtime deps (Playwright, Django, pandas, LangChain, scikit-learn, duckdb)
- `analytics.txt` — dbt-core + dbt-duckdb
- `crm.txt` — DjangoCRM (installed with `--no-deps` to skip mysqlclient)
- `local.txt` — test deps (pytest, pytest-django, factory-boy)

Dependencies are installed via `uv` (in Docker) or `pip` (locally via `make setup`).

### Configuration

Configuration is managed in `linkedin/conf.py`. All settings are loaded from a single `assets/accounts.secrets.yaml`
file (gitignored). This includes LLM API keys, campaign rate limits/timing, and account credentials.

### Testing

The project uses `pytest` with `pytest-django` for testing. Tests are in `tests/`. An autouse fixture runs
`setup_crm()` before each test to bootstrap the DjangoCRM database.

To run tests:

* **Using Docker**: `make test`
* **Locally**: `pytest`

### Error Handling

The application should crash on unexpected errors. `try...except` blocks should be used sparingly and only for
expected and recoverable errors.
