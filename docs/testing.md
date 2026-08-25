# Testing

The suite is pytest, mirroring the package structure under `tests/`. Mock at the **boundaries** — the BetterContact client, the hub API and the LLM — never inside business logic.

## Running

```bash
make test                 # full suite (native — tests do not run in Docker)

.venv/bin/pytest tests/test_qualify.py     # a single file
.venv/bin/pytest -k test_name              # a single test by name
```

## Layout

```
tests/
├── conftest.py                 # shared fixtures: operator (Django User), campaign, stubbed
│                               #   embeddings
├── factories.py                # factory-boy factories (LeadFactory → profile_url, etc.)
├── contacts/test_service.py    # the hub client (resolve / contribute), best-effort degradation
├── db/
│   └── test_deals.py           # Deal state ops
├── ml/
│   ├── test_embeddings.py      # FastEmbed embedding
│   └── test_qualifier.py       # GP + BALD selection, LLM qualification
├── test_bettercontact.py       # finder submit/poll + the discovery transport
├── test_lookup.py              # buy_address → check_lookup, the two-leg paid handshake
├── test_cycle.py               # the hierarchy: which row fires, and the one gate that declines
│                               #   — incl. TestTheFinderRunsWithoutASender, the pivot in tests
├── test_export.py              # the record schema, both serialisations, the two exclusions
├── test_business_time.py       # working-day arithmetic
├── test_discovery.py           # Lead Finder search + embed_row
├── test_discovery_wiring.py    # discover → qualify wiring
├── test_select.py              # the discovery walk: frontier, estimate, expand, retire
├── test_anchors.py             # synthetic ICP positives and their one-per-acceptance retirement
├── test_ready_pool.py          # GP rank gate
├── test_qualify.py             # qualification flow
└── test_onboarding{,_wizard}.py, test_llm.py, test_geo.py, test_db_option.py,
    test_version.py
```

*(The `tests/emails/` and `tests/agents/` trees, `test_sending_window.py` and
`test_mail_log_backfill.py` moved to OpenEmailSequence with the code they covered.)*

## Conventions

- **Mock at the boundary.** Patch the BetterContact HTTP client, the hub client and the pydantic-ai model — not the pipeline functions that call them.
- **CRM objects** come from `factories.py` (factory-boy) or direct model creation.
- **No browser, no network.** There is nothing to launch and no live API to hit; the tool is browserless and every external call is stubbed.

## What the suite does not cover

**A `find` that actually finds.** Every external call is stubbed, so no test — and no manual
run — has yet taken the happy path end to end with live `OPENOUTREACH_LLM_API_KEY` and
`OPENOUTREACH_BETTERCONTACT_API_KEY`. That includes the Claude Code plugin: the skill is
inventoried and fires correctly on the refusal paths (it runs `status`, reads
`onboarding_incomplete`, names the right variables and declines to accept the legal notice
for the user), but a session that returns leads has never been observed. Exercising it costs
one BetterContact credit per verified hit.
