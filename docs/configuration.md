# Configuration

All configuration lives in a single YAML file: `assets/accounts.secrets.yaml`. This file is gitignored and
contains credentials, LLM settings, campaign behavior, and account definitions.

To get started, copy the template:

```bash
cp assets/accounts.secrets.template.yaml assets/accounts.secrets.yaml
```

The file has three top-level sections: `env`, `campaign`, and `accounts`.

## LLM Configuration (`env:`)

Used for AI-powered follow-up messages and keyword generation. Any OpenAI-compatible provider works.

```yaml
env:
  LLM_API_KEY:  sk-...                      # required
  LLM_API_BASE: https://api.anthropic.com/v1 # provider base URL (optional)
  AI_MODEL:     claude-opus-4-6               # model identifier
```

| Field | Description | Default |
|:------|:------------|:--------|
| `LLM_API_KEY` | API key for an OpenAI-compatible provider. | (required) |
| `LLM_API_BASE` | Base URL for the API endpoint. | (none) |
| `AI_MODEL` | Model identifier for message generation and keyword generation. | `gpt-5.3-codex` |

These can also be set via `.env` file or environment variables (the YAML file takes precedence).

## Campaign Settings (`campaign:`)

Controls rate limits, timing, and behavior for each daemon lane.

```yaml
campaign:
  connect:
    daily_limit: 20
    weekly_limit: 100
  check_pending:
    recheck_after_hours: 24
  follow_up:
    daily_limit: 30
    existing_connections: false
  min_action_interval: 120
  enrich_min_interval: 1
  working_hours:
    start: "09:00"
    end: "18:00"
```

| Field | Type | Description | Default |
|:------|:-----|:------------|:--------|
| `connect.daily_limit` | integer | Max connection requests per day (resets at midnight). | `20` |
| `connect.weekly_limit` | integer | Max connection requests per week (resets on Monday). | `100` |
| `check_pending.recheck_after_hours` | float | Base interval (hours) before first check. Doubles per profile via exponential backoff. | `24` |
| `follow_up.daily_limit` | integer | Max follow-up messages per day (resets at midnight). | `30` |
| `follow_up.existing_connections` | boolean | `false` = mark pre-existing connections as IGNORED. `true` = send follow-ups to all connections. | `false` |
| `min_action_interval` | integer | Fixed minimum seconds between major actions (connect, follow-up). Rate limiters still enforce daily/weekly caps independently. | `120` |
| `enrich_min_interval` | integer | Floor (seconds) between enrichment API calls. | `1` |
| `working_hours.start` | string | Start of working window (`HH:MM`, OS local timezone). | `"09:00"` |
| `working_hours.end` | string | End of working window (`HH:MM`, OS local timezone). | `"18:00"` |

### How scheduling works

Major actions (connect, follow-up) fire at a fixed pace set by `min_action_interval` (default 120 seconds),
with ±20% random jitter for human-like pacing. Daily and weekly rate limiters independently cap totals.
Working hours act as a gate: the daemon only runs within the configured window and sleeps outside it.

## Account Configuration (`accounts:`)

Define one or more LinkedIn accounts. The daemon uses the first active account by default.

```yaml
accounts:
  jane_doe_main:
    active: true
    username: jane.doe@gmail.com
    password: SuperSecret123!
    subscribe_newsletter:
    followup_template: templates/messages/followup.j2
    followup_template_type: jinja
    booking_link: https://calendly.com/your-link
```

| Field | Type | Description | Default |
|:------|:-----|:------------|:--------|
| `active` | boolean | Enable/disable this account without removing it. | `true` |
| `username` | string | LinkedIn login email. | (required) |
| `password` | string | LinkedIn password. | (required) |
| `subscribe_newsletter` | boolean/null | Receive OpenOutreach updates. Auto-enabled for non-GDPR locations on first run (see below). | `null` |
| `followup_template` | string | Path to the follow-up message template (relative to `assets/`). | (required) |
| `followup_template_type` | string | Template engine: `"jinja"` for Jinja2, `"ai_prompt"` for LLM-generated messages. | (required) |
| `booking_link` | string | URL appended to follow-up messages (e.g. your calendar page). | (none) |

### GDPR Location Detection

On the first run, the daemon checks the logged-in user's LinkedIn location against a keyword list of
jurisdictions with opt-in email marketing laws (EU/EEA, UK, Switzerland, Canada, Brazil, Australia, Japan,
South Korea, New Zealand). If no keyword matches, an LLM call determines whether the location is protected.

- **Non-GDPR location**: `subscribe_newsletter` is auto-set to `true` for that account.
- **GDPR-protected location**: the existing config value is preserved (no override).
- **Unknown/empty location**: defaults to GDPR-protected (errs on the side of caution).

This check runs once per account (a marker file in `assets/cookies/` prevents re-runs). Setting
`subscribe_newsletter` explicitly in the config always takes precedence — the override only applies when
the field is `null` / unset.

### Derived Paths

The system automatically generates these paths per account:

- **Cookie file**: `assets/cookies/<handle>.json` (session persistence)

## Campaign Keywords

Campaign keywords are stored at `assets/campaign/campaign_keywords.yaml` and generated via interactive
onboarding on first run. They contain three lists used by the ML scorer:

```yaml
positive:
  - "machine learning"
  - "data science"
negative:
  - "recruiter"
  - "intern"
exploratory:
  - "startup"
  - "consulting"
```

See [Templating](./templating.md) for template configuration details.
