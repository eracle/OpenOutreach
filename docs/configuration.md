# Configuration

Configuration lives in two places: the **`SiteConfig`** DB singleton and per-campaign **`Campaign`** rows (both managed via interactive onboarding or Django Admin), plus a few hardcoded defaults in **`core/conf.py`**. There are no social-network credentials — OpenOutreach is browserless and uses no such account.

## Configure without a terminal

Every onboarding field is also an environment variable, so an install with no TTY (an agent, a
container, CI) never needs the wizard. Set these and run `openoutreach init` — or go straight to
`openoutreach find 10`, which does the same setup before it starts working:

| Variable | Step | Notes |
|:---------|:-----|:------|
| `OPENOUTREACH_PRODUCT_DESCRIPTION` | campaign | what it does, who it's for, the problem it solves |
| `OPENOUTREACH_CAMPAIGN_TARGET` | campaign | who you're going after and the outcome you want |
| `OPENOUTREACH_CAMPAIGN_NAME` | campaign | optional — defaults to "Email Outreach" |
| `OPENOUTREACH_AI_MODEL` | llm | `provider:model`, e.g. `anthropic:claude-sonnet-4-5-20250929` |
| `OPENOUTREACH_LLM_API_KEY` | llm | live-verified at boot; a bad key stops the run |
| `OPENOUTREACH_LLM_API_BASE` | llm | required for `openai_compatible:*`, ignored otherwise |
| `OPENOUTREACH_BETTERCONTACT_API_KEY` | bettercontact | powers discovery (free) and enrichment (paid) |
| `OPENOUTREACH_OPERATOR_EMAIL` | account | your own inbox — contacts key and newsletter target |
| `OPENOUTREACH_COUNTRY` | account | ISO 3166 alpha-2, e.g. `US` |
| `OPENOUTREACH_ACCEPT_LEGAL_NOTICE` | account | must be `true` — records that you accept the [Legal Notice](../LEGAL_NOTICE.md) |
| `OPENOUTREACH_NEWSLETTER` | account | optional, **defaults off** — set `true` to subscribe |

A step takes effect only when *all* of its variables are set; anything left over goes to the wizard on
a terminal, or exits listing exactly what is missing. `OPENOUTREACH_DB` (or `--db PATH`) points any
command at a different SQLite file.

## Operator / LLM / keys (`SiteConfig` singleton, pk=1)

Set during onboarding, editable in Django Admin. `SiteConfig` is the single source of truth for keys and the one persisted operator setting (country).

| Field | Description | Default |
|:------|:------------|:--------|
| `ai_model` | pydantic-ai `provider:model` id (e.g. `anthropic:claude-sonnet-4-5-...`); bare `gpt-*`/`claude-*`/`gemini-*` are auto-prefixed. Providers: openai/anthropic/google/groq/mistral/cohere/openai_compatible. | (required) |
| `llm_api_key` | API key for the chosen provider. Live-verified at onboarding. | (required) |
| `llm_api_base` | Base URL — **only** for `openai_compatible:*`. | (none) |
| `bettercontact_api_key` | [BetterContact](https://bettercontact.rocks?fpr=openoutreach) key — **free account, 40 credits, no card** (affiliate link, no markup to you). Powers **both** Lead Finder discovery (billed nothing) **and** work-email enrichment (one credit per verified address, only with `--emails`). **Blank disables discovery + enrichment.** | (empty) |
| `contacts_api_token` / `contacts_api_url` | Cross-operator contacts-store token (earned on first contribution) and URL (blank → default hub). | (empty) |
| `country_code` | ISO-3166 alpha-2. The only persisted operator setting — decides the newsletter opt-in default (`geo.is_gdpr_protected`) and whether this install contributes to the contacts store at all (`geo.is_eea_located`). | (from onboarding) |

The operator's own email and name live on the Django `User` (created at onboarding), not on `SiteConfig`.

## Campaign Settings (`Campaign` model)

Managed via Django Admin (`/admin/`) or created during onboarding.

| Field | Type | Description |
|:------|:-----|:------------|
| `product_docs` | text | Product/service description. Feeds ICP generation and qualification — **the whole input**. |
| `campaign_target` | text | Who you're going after + the outcome. Feeds the same. |
| `country_code` | string | ISO-3166 alpha-2 target country for this campaign's leads. |
| `headcount_min` / `headcount_max` | integer | Company-size band, applied to every discovery query. |
| `anchor_profiles` / `anchor_embeddings` | JSON / binary | Synthetic ideal profiles standing in for positives until real acceptances replace them, one per acceptance. |
| `model_blob` | binary | The per-campaign trained GP model (joblib). |

Discovery keeps no filter spec or page cursor on the campaign: the keyword sets it has fired, and how far each was paged, live in their own `Keyword` / node rows.

## Sending mailboxes — there are none

The `Mailbox` model, the SMTP/IMAP credentials, the per-box signature, the measured daily cap and the
send-spacing clock all moved to [OpenOutSend](https://github.com/eracle/OpenOutSend) with
the sending leg. **Nothing here needs a mailbox**, and onboarding no longer asks for one.

## Newsletter jurisdiction default

At onboarding you enter your `country_code`. If it is **not** an opt-in jurisdiction (EU/EEA, UK, Switzerland, Canada, Brazil, Australia, Japan, South Korea, New Zealand), the newsletter default is on; otherwise it is off. An explicit yes always subscribes. The check reads `core/geo.is_gdpr_protected` — country comes from onboarding, never from any account lookup.

## Hardcoded Defaults (`core/conf.py`)

Not user-configurable per campaign; edit the source to change.

| Key | Value | Description |
|:----|:------|:------------|
| `COLLECT_BACKOFF_BASE_S` / `COLLECT_BACKOFF_MAX_S` | `5` / `30d` | The lookup poll doubles its delay on every still-running attempt and **never gives up** — an unterminated job is queued, not lost, so the leg keeps the same `request_id` rather than abandoning the deal and paying for a second job. MAX rails the interval only, so the schedule stays representable. |
| `CAMPAIGN_CONFIG.min_gp_confidence` | `0.75` | GP probability threshold for promoting `QUALIFIED → READY_TO_FIND_EMAIL`. **A spend gate on the paid lookup and nothing else** — not a quality score, and deliberately absent from the export. |
| `CAMPAIGN_CONFIG.qualification_n_mc_samples` | `100` | Monte Carlo samples for BALD. |
| `CAMPAIGN_CONFIG.embedding_model` | `BAAI/bge-small-en-v1.5` | FastEmbed model for 384-dim embeddings. |

**There is no spend cap setting, because the command line is the cap.** A run cannot spend at all
unless you ask it to: `--emails` permits the paid lookup, and the `emails` unit implies it, so a bare
`find 10` is free however many deals have queued up past the confidence gate. When you do ask, the
number you type is the budget — one credit is one verified address, so `openoutreach find 10 emails`
cannot cost more than ten. (Beyond that, your
own prepaid balance at the provider, which the provider enforces and this software cannot see.)
Discovery and qualification are ungated entirely: searching is free and qualifying costs one call
against your own LLM key.

**There is no timeout setting either**, and there should not be one. A run ends when its goal is met or
when nothing can advance — and every wait that matters is already written on the row that is waiting
(`Deal.not_before`, the doubling lookup backoff, `urllib3`'s 429 retry). A clock over the top of those
would be a second answer to a question they already answer, and a worse one: it knows nothing about
*why* the run is waiting. If you want a deadline, `Ctrl-C` (or your agent's own timeout) prints the
rows found so far and exits non-zero.

*(Gone with the sending leg: `SEND_WINDOW_*`, `MIN_SEND_INTERVAL_SECONDS`, `SEND_INTERVAL_JITTER_*`,
`WARM_*`, `COLLECT_TODAY_HORIZON_S`, `MAIL_PASS_INTERVAL_S`.)*

## Working-day arithmetic

`core/business_time.py` only *measures*: whole Mon–Fri days between two dates
(`business_days_between`). It existed to tell the outreach agent how old a thread was; with no agent,
nothing in the pipeline calls it. Public holidays are not modelled — that data is per-country and
per-year.
