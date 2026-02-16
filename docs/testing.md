# Testing

This document describes the testing setup and conventions for OpenOutreach.

## Framework & Tools

- **pytest** — test runner with `pytest-django` integration
- **pytest-mock** — mocking via `mocker` fixture
- **factory-boy** — test data generation
- **pytest-cov** — coverage reporting

## Running Tests

```bash
# Run all tests
pytest

# Run a single test file
pytest tests/api/test_voyager.py

# Run a single test by name
pytest -k test_name

# Run via Docker
make test
```

## Configuration

Tests are configured via `pytest.ini` in the project root:

```ini
[pytest]
pythonpath = .
testpaths = tests
DJANGO_SETTINGS_MODULE = linkedin.django_settings
```

The `DJANGO_SETTINGS_MODULE` setting ensures Django and DjangoCRM models are available in all tests.

## CRM Setup Fixture

An autouse fixture in `tests/conftest.py` runs `setup_crm()` before each test to bootstrap the CRM database
(Deal Stages, Closing Reasons, Department, etc.). This ensures every test has a clean, consistent CRM state.

```python
@pytest.fixture(autouse=True)
def _setup_crm(db):
    from linkedin.management.setup_crm import setup_crm
    setup_crm()
```

The `db` fixture (from `pytest-django`) handles database creation and transaction rollback per test.

## Test Organization

Tests live in `tests/` and mirror the source layout:

```
tests/
├── conftest.py              # Shared fixtures (autouse setup_crm)
├── factories.py             # Factory-boy factories for CRM models
├── api/
│   └── test_voyager.py      # Voyager API response parsing
├── db/
│   └── test_profiles.py     # CRM profile CRUD operations
├── lanes/
│   ├── test_lanes.py        # Daemon lane logic
│   └── test_qualify.py      # Qualification lane logic
├── ml/
│   ├── test_qualifier.py    # Bayesian qualifier (GPC + BALD)
│   ├── test_embeddings.py   # DuckDB embedding store
│   └── test_profile_text.py # Profile text builder
├── test_conf.py             # Configuration loading
├── test_emails.py           # Newsletter subscription
├── test_gdpr.py             # GDPR location detection
├── test_onboarding.py       # Interactive onboarding
├── test_rate_limiter.py     # Rate limiter
├── test_templates.py        # Message template rendering
└── fixtures/
    └── profiles/            # Sample Voyager API JSON responses
```

## Conventions

- **Mocking**: External dependencies (Playwright, LinkedIn API, LLM calls) are always mocked in unit tests.
- **Crash on unexpected errors**: Tests should not swallow exceptions. Only expected, recoverable errors should
  be caught (matching the application's error handling convention).
- **Test data**: Use factory-boy factories or direct model creation for CRM objects. Sample Voyager API JSON
  responses live in `tests/fixtures/profiles/`.
