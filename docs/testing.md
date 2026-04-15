# Testing

This document describes the testing setup and conventions for OpenOutreach.

## Framework & Tools

- **pytest** — test runner with `pytest-django` integration
- **pytest-mock** — mocking via `mocker` fixture
- **factory-boy** — test data generation
- **pytest-cov** — coverage reporting

## Running Tests

```bash
# Run all tests locally
make test

# Run a single test file
pytest tests/api/test_voyager.py

# Run a single test by name
pytest -k test_name

# Run via Docker
make docker-test
```

## Configuration

Tests are configured via `pytest.ini` in the project root:

```ini
[pytest]
pythonpath = .
testpaths = tests
DJANGO_SETTINGS_MODULE = linkedin.django_settings
```

The `DJANGO_SETTINGS_MODULE` setting ensures Django models are available in all tests.

## CRM Setup Fixture

An autouse fixture in `tests/conftest.py` runs `setup_crm()` before each test to bootstrap the CRM database
(default Site, etc.). This ensures every test has a clean, consistent CRM state.

```python
@pytest.fixture(autouse=True)
def _ensure_crm_data(db):
    Group.objects.get_or_create(name="co-workers")
    setup_crm()
```

The `db` fixture (from `pytest-django`) handles database creation and transaction rollback per test.

## Test Organization

Tests live in `tests/` and mirror the source layout:

```
tests/
├── conftest.py              # Shared fixtures (autouse CRM setup, FakeAccountSession)
├── factories.py             # Factory-boy factories for CRM models
├── api/
│   └── test_voyager.py      # Voyager API response parsing
├── db/
│   ├── test_profiles.py     # CRM profile CRUD operations
│   └── test_lazy_enrichment.py  # Lazy enrichment/embedding fallbacks
├── tasks/
│   └── test_tasks.py        # Task handler logic (connect, check_pending, follow_up)
├── ml/
│   ├── test_qualifier.py    # Bayesian qualifier (GPR + BALD)
│   ├── test_embeddings.py   # Embedding storage on Lead model (Django/SQLite)
│   └── test_profile_text.py # Profile text builder
├── test_action_log.py       # ActionLog rate limiting
├── test_conf.py             # Configuration loading
├── test_emails.py           # Newsletter subscription
├── test_gdpr.py             # GDPR location detection
├── test_reconcile.py        # Task queue reconciliation (scheduler.reconcile)
├── test_onboarding.py       # Interactive onboarding
├── test_pools.py            # Candidate pool generators
├── test_qualify.py          # Qualification pipeline
├── test_ready_pool.py       # Ready-to-connect pool / GP gate
└── fixtures/
    ├── profiles/            # Sample Voyager API JSON responses
    └── pages/               # Sample HTML pages
```

## Conventions

- **Mocking**: External dependencies (Playwright, LinkedIn API, LLM calls) are always mocked in unit tests.
- **Crash on unexpected errors**: Tests should not swallow exceptions. Only expected, recoverable errors should
  be caught (matching the application's error handling convention).
- **Test data**: Use factory-boy factories or direct model creation for CRM objects. Sample Voyager API JSON
  responses live in `tests/fixtures/profiles/`.
