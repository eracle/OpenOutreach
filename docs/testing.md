# Testing

The suite is pytest, mirroring the package structure under `tests/`. Mock at the **boundaries** — the BetterContact client, the hub API, the LLM, and SMTP/IMAP — never inside business logic.

## Running

```bash
make test                 # full suite (local)
make docker-test          # full suite in Docker

.venv/bin/pytest tests/test_qualify.py     # a single file
.venv/bin/pytest -k test_name              # a single test by name
```

## Layout

```
tests/
├── conftest.py                 # shared fixtures (fake_session: Django User + SiteConfig, no browser)
├── factories.py                # factory-boy factories (LeadFactory → profile_url, etc.)
├── agents/test_follow_up.py    # the agentic follow-up decision loop
├── contacts/test_service.py    # the hub client (resolve / contribute), best-effort degradation
├── db/
│   ├── test_deals.py           # Deal state ops
│   └── test_summaries.py       # mem0-style profile/chat summaries
├── emails/
│   ├── test_bettercontact.py   # finder submit/poll + discovery transport
│   ├── test_find_email.py      # the find_email → collect_email legs
│   ├── test_mailbox.py         # per-box daily-cap pacing
│   ├── test_send.py            # opener send + EMAILED transition
│   └── test_smtp.py            # SMTP auth-check (port-based transport)
├── ml/
│   ├── test_embeddings.py      # FastEmbed embedding
│   └── test_qualifier.py       # GP + BALD selection, LLM qualification
├── test_discovery.py           # Lead Finder search + embed_row
├── test_discovery_wiring.py    # discover → qualify wiring
├── test_pools.py               # composable candidate generators
├── test_ready_pool.py          # GP rank gate
├── test_qualify.py             # qualification flow
└── test_onboarding{,_wizard}.py, test_llm.py, test_geo.py, test_tz_country.py, test_schedule.py
```

## Conventions

- **Mock at the boundary.** Patch the BetterContact HTTP client, the hub client, the pydantic-ai model, and SMTP/IMAP transports — not the pipeline functions that call them.
- **CRM objects** come from `factories.py` (factory-boy) or direct model creation.
- **No browser, no network.** There is nothing to launch and no live API to hit; the daemon is browserless and every external call is stubbed.
