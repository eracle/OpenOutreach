# Architecture

Detailed module documentation for OpenOutreach. See `CLAUDE.md` for rules and quick reference.

OpenOutreach is a browserless, **email-first** AI sales agent: it learns a campaign's ICP
and runs the whole funnel — **define ICP → discover → qualify → rank → find email → agentic
email** — off licensed data, with no LinkedIn account and no scraping.

## Project Layout

All source lives in the single `openoutreach/` package; Django apps are nested inside it
(dotted `AppConfig.name`, short labels). One engine, one outreach channel:

```
manage.py
tests/
openoutreach/
  settings.py        # Django settings (SQLite at data/db.sqlite3)
  urls.py
  discovery.py       # Lead Finder client (ICP search + row embedding) — the top of the funnel
  core/              # engine app (label: core) — daemon, task queue + scheduler,
                     #   Campaign/SiteConfig/Task models, llm.py, conf.py, onboarding,
                     #   ML (qualifier/embeddings/kit), discovery+qualify pipeline,
                     #   the two agents, db/ helpers, session, geo, management commands,
                     #   vendored mem0
  emails/            # channel app (label: emails) — enrichment (BetterContact), Mailbox +
                     #   import + SMTP/IMAP, sender/inbox, the three task handlers
  crm/               # app (label: crm) — Lead, Deal
  chat/              # app (label: chat) — ChatMessage (the per-Deal conversation)
  legacy/            # model-less app (label: legacy) — migration-history anchor only
  contacts/          # central contacts-store client (service.py only — no models, not an app)
```

Layering: `core` owns orchestration, the ML/discovery/qualify pipeline, and the
channel-agnostic models; the `emails` app owns the enrichment + send/read mechanics and the
task handlers. `core` imports channel code only at wiring points (the daemon's handler map).

**No LinkedIn.** The browser, Voyager API, connect/check_pending, and the `linkedin_cli`
dependency were removed in the email-first pivot. The `legacy` app is intentionally
model-less — it exists only to anchor migration history that `core`/`crm` depend on so
existing installs stay on a forward-only, backward-compatible migration graph (the retired
`LinkedInProfile`/`SearchKeyword`/`ActionLog` models were deleted in `legacy/0012`).

## Entry Flow

`manage.py` — stock Django management entrypoint. Bare `python manage.py` (no subcommand, or a
leading flag) defaults to `rundaemon`.

### `rundaemon` management command (`management/commands/rundaemon.py`)

Startup sequence:
1. **Configure logging** — level from `--verbosity`, banner, noisy third-party loggers silenced (`core/logging.py`).
2. **Ensure DB** — `migrate --no-input` (the custom migrate; see below) + `setup_crm` (idempotent).
3. **Onboard** — if `missing_keys()` is non-empty: interactive wizard on a TTY, else print what's missing and exit (no TTY, no silent partial start).
4. **Create session** — validate `llm_api_key`, resolve the active operator `User`, build an `OperatorSession`, default its campaign to the first one.
5. **Run** — `run_daemon(session)`.

Docker's `start` script `exec`s `python manage.py rundaemon` (no Xvfb/VNC — there is no browser).

### Other management commands

- `migrate` — **overridden** (`management/commands/migrate.py` + `core/migration_compat.py`): before Django's migration-consistency check runs, it relabels any `linkedin` rows in `django_migrations` to `legacy`, so a pre-pivot DB upgrades with a plain `migrate` (no manual SQL, no `--fake`). Idempotent no-op on fresh installs.
- `setup_crm` — idempotent CRM bootstrap (default Site).
- `reset_data` — wipe pipeline data for a fresh run.

## Onboarding (`core/onboarding.py`)

Email-first and mostly imperative. `OnboardConfig` is a small dataclass of the **declarative**
answers only (product/objective/booking link, LLM creds, country, newsletter, legal); the two
credential steps write directly, and the operator email is read back from the stored mailbox.

`onboard_interactive()` runs the ordered steps, each skipped if already satisfied (so a partial
onboarding resumes):

```
1 product description   2 campaign objective   3 LLM (live-verified via verify_llm_credentials)
4 mailbox / SMTP   (paste an App-Passwords sheet → SMTP auth-check → Mailbox rows; loops until ≥1 authenticates)
5 BetterContact key  (mandatory — the SAME key powers Lead Finder discovery AND enrichment)
6 country            (timezone + email jurisdiction)
7 newsletter (opt-in, country-dependent default) + legal (required gate)
```

- The operator's email is **not asked** — it is the connected mailbox's `from_address`, so the operator `User` is created *after* the mailbox exists (`apply()` → `_create_account`).
- `missing_keys()` reports the declarative fields **plus** the two credential steps (`mailbox`, `bettercontact`) and the account itself, so the daemon knows onboarding is incomplete until every gate passes.
- The newsletter opt-in **default** is jurisdiction-aware (off in GDPR/opt-in countries via `core/geo.is_gdpr_protected`), but an explicit yes always subscribes (lawful consent anywhere).
- Single write path: `apply(config)` — idempotent; creates the missing Campaign, writes LLM config + `country_code` to `SiteConfig`, creates the operator `User` from the mailbox email, and subscribes the newsletter once (a country-dependent, one-time action — no stored flag).
- The interactive wizard is vendored: `onboarding_wizard.py` (questionary/prompt_toolkit step engine with back/skip/cancel, `Password`/`IntText`/`MultilineText` primitives) + `onboarding_prompts.py` (`CAMPAIGN_QUESTIONS`/`LLM_QUESTIONS`/`JURISDICTION_QUESTIONS`). No external `openoutreach` package dependency.

## Deal State Machine

`crm/models/deal.py:DealState` (OpenOutreach-owned `TextChoices`) is the whole funnel — a lead
is discovered and qualified **without** an email in hand (Lead Finder returns firmographics, not
addresses), so the funnel first *finds* the email and then *talks*:

```
QUALIFIED ──(GP rank gate)──▶ READY_TO_FIND_EMAIL ──(find_email task)──▶ hit:  READY_TO_EMAIL
 discovered + qualified        ranked, awaiting the       BetterContact,        miss: FAILED (reason="no email")
 (no email yet)                paid lookup                checked once                 │
                                                                                       ▼
                          READY_TO_EMAIL ──(email opener)──▶ EMAILED ⟲ (agentic follow-up) ──▶ COMPLETED / FAILED
                                                             read replies (IMAP) → agent: send / wait / complete
                                                             send: threaded SMTP reply, re-arm next_follow_up_at
```

- **`READY_TO_FIND_EMAIL`** — passed the **GP confidence gate** (`ready_pool.promote_to_ready` above `min_gp_confidence`); queued for the *paid* BetterContact lookup (one credit per verified hit). The gate rations spend to leads the model is confident about.
- **`READY_TO_EMAIL`** — an address exists; queued for the opener. A cheap, **ungated** FIFO send-queue paced only by the per-box daily cap (no ranking step).
- **`EMAILED`** — the opener has been sent; the agentic follow-up loop reads IMAP replies and decides send/wait/complete, paced by the agent's own `follow_up_hours` (stamped on `Deal.next_follow_up_at`), until a terminal `COMPLETED`/`FAILED`.

**`find_email` is tri-state.** hit → `READY_TO_EMAIL`; **miss** (finder ran, no address) → `FAILED`, `reason="no email"`, **outcome blank** — critically not `wrong_fit`, because the ML labeler reads `FAILED+wrong_fit` as a negative and *skips* every other `FAILED` deal, so a lead we simply couldn't find is ML-skipped, never scored a bad fit; **couldn't-run** (no key / out of credits / API down — the lookup never happened) → stays `READY_TO_FIND_EMAIL` to retry. An out-of-credits response must map to couldn't-run, never miss.

`crm/models/deal.py:Outcome` (TextChoices): converted, not_interested, wrong_fit, no_budget,
has_solution, bad_timing, unresponsive, unknown — on `Deal.outcome`. `Lead.disqualified=True` =
permanent account-level exclusion (never given a new deal). LLM qualification rejections =
`FAILED` deals with `wrong_fit` outcome (campaign-scoped). Pre-Deal Lead states are implicit:
url-only (a `Lead` row with a null `embedding`) vs embedded (has an `embedding` + `profile_text`,
awaiting qualification).

*(The LinkedIn connect leg — `READY_TO_CONNECT`/`PENDING`/`CONNECTED`, the connect/check_pending
retry+backoff columns — was removed with the channel. Existing deals stranded at those states are
remapped to `QUALIFIED` on upgrade so they re-enter the email funnel.)*

## Task Queue

Persistent queue backed by the `Task` model. Worker loop in `core/daemon.py`:
`seconds_until_active()` guard pauses outside the daily active-hours window (single contiguous
window, no weekend skip) → `claim_next` (**email-first then oldest-due**, see
`TaskQuerySet.pending`) → set campaign on session → RUNNING → dispatch via `_HANDLERS` →
COMPLETED/FAILED. A `ModelHTTPError` from the LLM stops the daemon with a clear config hint; any
other exception fails just that task and continues. Between tasks a `_HumanRhythmBreak` injects
random burst/break pauses, and a `Heartbeat` logs an `alive — …` line so the daemon never goes
silent for more than 5 minutes. `reconcile(session)` runs once before the loop, so a fresh start
recovers crash-stale RUNNING tasks and flushes ready email before serving.

Task rows are **lazy**: `payload = {"campaign_id": <id>}` only — no deal reference. The handler
resolves a concrete target at execution time via a single eligibility query. Slot creation is
centralized in `core/scheduler.py`; no other module inserts `Task` rows. The scheduler has two
flavours of slot creation plus reconcile:

1. **Window planner** (rate-limited leg) — `plan_find_email_window`: when no PENDING find_email task exists for a campaign, insert `1 immediate + (n-1) Poisson-spaced` lazy rows across the working portion of the next 24h. `n = FIND_EMAIL_DAILY_CAP` (a flat paid-spend guard, since a verified hit costs a credit). The leading immediate slot kills the cold-start ramp. No-op unless a mailbox is connected **and** BetterContact is configured. Spacing primitives: `working_seconds_in_window` + `poisson_slot_times` (uniform order-statistic → exactly `n` slots, mean spacing `T/(n+1)` in working time; `tz_name=None` → no gating).
2. **Eager drain** (email legs — no anti-bot rhythm to fake) — `flush_email_queue` emits an immediate, un-spaced, un-ranked slot for every `READY_TO_EMAIL` deal; `flush_follow_up_queue` emits one for every `EMAILED` deal whose `next_follow_up_at` is due. Both are capped by pool-wide per-box headroom (`Mailbox.objects.remaining_today()`) and no-op while a PENDING task of their type exists.
3. **`reconcile(session)`** — recovers stale RUNNING tasks, then per campaign runs the planner and both eager drains. Called on startup and whenever the queue has no ready task; combined with email-first claim ordering, the startup call makes the daemon send any ready email before anything else.

**Handlers** (in `emails/tasks/`, signature `handle_*(task, session, qualifiers)`):

1. **`handle_find_email`** (`tasks/find_email.py`) — drives the discovery→qualify→rank chain to one top-ranked `READY_TO_FIND_EMAIL` candidate (freemium campaigns draw from the kit-ranked pool and mint the Deal on the fly), then resolves the email cheapest-source-first: free hub cache (`contacts.resolve`) → paid `bettercontact` (tri-state). Routes hit → `READY_TO_EMAIL`, miss → `FAILED`. No-op with no mailbox. A fresh paid hit is contributed back to the hub.
2. **`handle_email`** (`tasks/send.py`) — picks the least-loaded under-cap `Mailbox` + the oldest `READY_TO_EMAIL` deal (`core.db.deals.get_emailable_deals`), materializes the profile summary, composes the opener (`core/agents/email_opener.py`), sends over SMTP (`emails/sender.py`, BCC = the operator's own address), then `_record_sent_email` writes the email fields, the outgoing opener `ChatMessage`, and `state=EMAILED` — send record + state on one row, so no double-send window. `next_follow_up_at` is seeded from the opener agent's own `follow_up_hours`.
3. **`handle_follow_up`** (`tasks/follow_up.py`) — picks the oldest due `EMAILED` deal whose bound box has headroom, runs `run_follow_up_agent` (reads IMAP replies via `emails/inbox.py`, decides), then executes: `send_message` → threaded SMTP reply (`In-Reply-To` = latest message, `References` = thread root) + re-arm the clock; `mark_completed` → `COMPLETED` with the agent's outcome; `wait` → push `next_follow_up_at` out.

## Qualification ML Pipeline

GPR (sklearn, `ConstantKernel * RBF` inside `Pipeline(StandardScaler, GPR)`) with BALD active
learning, over 384-dim FastEmbed embeddings (`BAAI/bge-small-en-v1.5`) stored on `Lead.embedding`;
per-campaign models persisted in `Campaign.model_blob` (joblib, `compress=3`).

1. **Discovery** feeds the pool: `core/pipeline/discover.py:discover` pages the campaign ICP (`core/pipeline/icp.py`, cached on `Campaign.icp_filters`) from Lead Finder into embedded `Lead`s; the qualify chain calls it when its candidate pool goes dry.
2. **Balance-driven selection** — `n_negatives > n_positives` → exploit (highest P); else → explore (highest BALD).
3. **LLM decision** — every qualify decision is an LLM call (`qualify_lead.j2` reading the lead's stored `profile_text`); the GP is used only for candidate selection and the confidence gate.
4. **Rank gate** — `ready_pool.promote_to_ready` promotes `QUALIFIED → READY_TO_FIND_EMAIL` when `P(f>0.5)` exceeds `min_gp_confidence` (0.9), so a paid credit is only ever spent on a ranked lead.

Cold start returns None until ≥2 labels of both classes; the daemon warm-starts each campaign's GP
from `Lead.get_labeled_arrays` at boot. Freemium campaigns use a pre-trained `KitQualifier`
(HuggingFace kit) instead of a warm-started GP.

## Django Apps

- **`core`** — Engine: `SiteConfig`, `Campaign`, `Task` models; daemon, scheduler, LLM factory, onboarding, the ML/discovery/qualify pipeline, the two agents, session, geo, vendored mem0.
- **`emails`** — The email channel. `bettercontact.py` (paid finder: `resolve_email` facade, `find_email`, the shared `submit_and_poll` transport, `is_configured`, `BetterContactQuery`/`Result`/`Unavailable`); `models.py` (`Mailbox` + the per-box daily-cap pacing manager + `has_mailbox()`); `icemail.py` (`parse_mailboxes` — the App-Passwords sheet), `smtp.py` (`verify_auth`), `mailbox_setup.py` (`import_mailboxes` → parse → auth-check → store); `sender.py` (`send_email` over SMTP+STARTTLS, threading headers, BCC-to-operator); `inbox.py` (`sync_inbox` — IMAP reply-reader); `newsletter.py` (`subscribe_to_newsletter`, Brevo); `tasks/` (the three handlers).
- **`crm`** — `Lead` (identity + embedding + email) and `Deal` (`crm/models/lead.py`, `crm/models/deal.py`); also defines `DealState` and `Outcome`.
- **`chat`** — `ChatMessage`, FK to the owning `Deal` (the per-(lead, campaign) conversation; the opener + every reply are rows here).
- **`legacy`** — model-less; migration-history anchor only (see Project Layout).
- **`contacts`** — the central contacts-store client (`service.py`, no models, **not** an installed app) — "the hub" (`hub.openoutreach.app`), logged under the `hub:` prefix. `resolve(lead)` (free read-back before the paid finder) and `contribute(session, lead, emails, origin)` (give-back, non-EEA only, registers on first use). Both best-effort; an outage or missing token degrades to a no-op.

History note: the engine models (`SiteConfig`/`Campaign`/`Task`) lived in the LinkedIn app until
mid-2026 and were moved to `core` (state-only + table renames); the LinkedIn app was then emptied
to models and renamed `legacy`.

## CRM Data Model

- **SiteConfig** (`core/models.py`) — Singleton (pk=1). `ai_model` (pydantic-ai `provider:model`; valid providers openai/anthropic/google/groq/mistral/cohere/openai_compatible), `llm_api_key`, `llm_api_base` (only for `openai_compatible:*`), `bettercontact_api_key` (blank disables discovery + enrichment), `contacts_api_token`/`contacts_api_url` (token earned on first contribution; blank URL → default hub), `country_code` (ISO-3166 alpha-2 — the only persisted operator setting; drives the active-hours timezone via `tz_country` and the email-jurisdiction rules via `core/geo`). `SiteConfig.load()`; `core/llm.get_llm_model()` turns it into a `pydantic_ai.models.Model`.
- **Campaign** (`core/models.py`) — `name` (unique), `users` (M2M to `User`), `product_docs`, `campaign_objective`, `booking_link`, `is_freemium`, `action_fraction`, `seed_public_ids`, `model_blob` (per-campaign GP). Discovery: `icp_filters` (the cached Lead Finder spec `{"filters": …, "country_code": …}`, generated once by the LLM) + `discovery_offset` (the page cursor that lets discovery advance across cycles/restarts).
- **Lead** (`crm/models/lead.py`) — Keyed on `profile_url` (unique — the discovery provider's per-person URL, the opaque identity/lookup key, **stored, never fetched**). `country_code` (stamped from the discovery ICP; drives the contacts-store geo-gate; blank → never contributed). `embedding` (384-dim float32 BinaryField, built at discovery). `profile_text` (the firmographic text — headline/location/industry/title/company/company-description — built from the Lead Finder row at discovery, the LLM qualifier's input; no re-scrape). `email` (the BetterContact result; null = not found/unresolved). `disqualified`. `resolve_email()` populates `email` via the finder (tri-state True/False/None, cached on a hit) — called from the find-email leg, not inline at qualification. `to_profile_dict()` → `{lead_id, profile_url}`; `embedding_array` for numpy; `get_labeled_arrays(campaign)` → (X, y) for GP warm start (non-FAILED → 1, FAILED+wrong_fit → 0, other FAILED → skipped). Created browserless via `core/db/leads.create_lead(row, country_code)` (or freemium seeds via `core/setup/freemium.py`) — there are no scrape accessors.
- **Deal** (`crm/models/deal.py`) — campaign-scoped (`unique(lead, campaign)`). `state` (`DealState`), `outcome` (`Outcome`), `reason` (free text). **Email fields:** `mailbox` (FK to the sending `Mailbox` — the per-box-cap counting key, reply anchor, sticky thread box), `email_subject` (the opener's subject, reused as "Re: …"), `email_sent_at` (opener audit timestamp), `email_message_id` (the immutable thread root the IMAP reader matches replies on), `next_follow_up_at` (the agentic-loop cursor — seeded by the opener, re-armed each turn). `profile_summary` / `chat_summary` (lazy mem0-style JSON fact lists, campaign-scoped). `creation_date`, `update_date`.
- **Task** (`core/models.py`) — `task_type` (find_email/follow_up/email), `status` (pending/running/completed/failed), `scheduled_at`, `payload`, timestamps. `TaskQuerySet.pending()` orders **EMAIL first, then oldest `scheduled_at`**; `claim_next()`/`seconds_to_next()`. Composite index on `(status, scheduled_at)`.
- **ChatMessage** (`chat/models.py`) — FK to the owning **Deal** (`related_name="messages"`). `content`, `is_outgoing`, `owner`, `external_id` (message identity for per-deal dedup — the email Message-ID; legacy LinkedIn rows hold a Voyager entityUrn), `answer_to`/`topic` (self FKs), `creation_date`. Dedup: `unique(deal, external_id)`. The opener + every reply are rows here; `Mailbox.sent_today()` counts the outgoing ones for the per-box cap.
- **Mailbox** (`emails/models.py`) — one SMTP inbox: `host`/`port` (default `smtp.gmail.com:587`), `imap_host`/`imap_port` (default `imap.gmail.com:993` — the read side for the reply loop, same app password), `username`, `password`, `from_address`, `daily_limit` (warm-safe sends/day, default `DEFAULT_EMAIL_DAILY_LIMIT`). A row exists only once its credentials pass the import auth-check (no health API). Manager: `remaining_today()` (Σ per-box headroom), `least_loaded_under_cap()`; instance `sent_today()` (outgoing ChatMessages on this box's deals since local midnight), `headroom_today()`. `has_mailbox()` is the "email is a viable channel" gate.

## Key Modules

Paths relative to `openoutreach/`.

- **`core/daemon.py`** — worker loop with active-hours guard (`seconds_until_active`), `Heartbeat` + `_HumanRhythmBreak` pacing, `_build_qualifiers` (per-campaign GP warm-start / freemium `KitQualifier`), freemium kit loading (`fetch_kit` → `import_freemium_campaign` → `seed_profiles`), startup + idle `reconcile`.
- **`core/scheduler.py`** — the only creator of `Task` rows: `plan_find_email_window` (Poisson window, gated on mailbox + finder), `flush_email_queue` / `flush_follow_up_queue` (eager drains), `reconcile`, and the `poisson_slot_times`/`working_seconds_in_window` spacing primitives.
- **`core/session.py`** — `OperatorSession` (browserless): holds the Django `User`, `campaigns` (cached), `self_profile` (synthesized from the user + `SiteConfig` country — not scraped), `active_timezone` (`ACTIVE_TIMEZONE` override else the operator's country via `tz_country`, else None). `get_active_user()`, `get_or_create_session()`.
- **`discovery.py`** — Lead Finder client: `search(filters, limit, offset)` (ICP search → lead rows, free), `profile_text_for(row)` + `embed_row(row)` (the qualifier's text/vector, built in the pre-pivot field order so old and new vectors stay comparable). Shares `submit_and_poll` with `emails/bettercontact.py`.
- **`core/pipeline/`** — `icp.py` (`icp_for`/`generate_icp_spec`: one LLM pass → Lead Finder filters, cached on `Campaign.icp_filters`), `discover.py` (`discover`: page the ICP into embedded Leads, advance `discovery_offset`), `qualify.py` (`run_qualification` / `fetch_qualification_candidates` — reads `Lead.profile_text`, no scrape), `ready_pool.py` (GP gate: `promote_to_ready`, `find_ready_candidate`), `pools.py` (composable generators `qualify_source → ready_source → find_candidate`; discovers a fresh page when dry), `freemium_pool.py` (`find_freemium_candidate`).
- **`core/ml/`** — `qualifier.py` (`Qualifier` protocol, `BayesianQualifier`, `KitQualifier`, `qualify_with_llm`, `format_prediction`), `embeddings.py` (`embed_text`/`embed_texts`, cached FastEmbed model), `hub.py` (`fetch_kit` + the download/load helpers — the HuggingFace campaign kit).
- **`core/setup/freemium.py`** — `import_freemium_campaign` (adds the Django `User`), `seed_profiles` (seeds get a LinkedIn-shaped opaque `profile_url`, embeddings deferred to discovery), `profile_url_from_slug`.
- **`core/db/leads.py`** — `create_lead(row, country_code)` (persist one Lead Finder row as an embedded Lead, idempotent), `promote_lead_to_deal`, `disqualify_lead`.
- **`core/db/deals.py`** — Deal state ops: `set_profile_state`, the state-pool queries (`get_qualified_profiles`, `get_ready_to_find_email_profiles`, `get_emailable_deals`), `create_disqualified_deal`, `create_freemium_deal`. `_STATE_LOG_STYLE` colors the funnel transitions in the log.
- **`core/db/summaries.py`** — the single mem0-style LLM boundary. `materialize_profile_summary_if_missing(deal, session)` builds `profile_summary` on first follow-up touch from the lead's stored `profile_text` (**no re-scrape**); `update_chat_summary(deal, new_messages, *, seller_name)` folds newly-read replies into `chat_summary` via `reconcile_facts` (mem0 ADD/UPDATE/DELETE/NONE); an identity binding (`seller_name_from(session)`) keeps the LLM from misattributing seller-name greetings in a lead reply. mem0's update prompt is vendored under `core/vendor/mem0/` (no `mem0ai` runtime dep).
- **`core/agents/`** — `prompt.py` (shared `render`/`base_context`/`_format_facts`; both agents extend `_outreach_base.j2`), `email_opener.py` (`compose_opener_email` → `EmailDraft{subject, body, follow_up_hours}`, the one-shot cold opener), `follow_up.py` (`run_follow_up_agent` → `FollowUpDecision{action, message?, outcome?, follow_up_hours}`; reads the IMAP-synced thread + a recency window of verbatim messages; single structured LLM call, no tool loop).
- **`core/llm.py`** — `get_llm_model()` factory (reads `SiteConfig`, `split_model_id` parses the provider out of `ai_model`, dispatches to the per-provider builder), `build_llm_model` (from explicit creds), `verify_llm_credentials` (one live ping, tenacity-retried, used by onboarding), and `run_agent_sync(coro)` — the sync boundary that drives async pydantic-ai on a dedicated long-lived worker-thread loop (never `Agent.run_sync`, whose anyio portal poisons the caller thread's loop slot; never per-call `asyncio.run`, which closes loops the SDK HTTP clients still reference).
- **`core/geo.py`** — jurisdiction sets + predicates: `is_gdpr_protected` (broad opt-in set, drives the newsletter default) and `is_eea_located` / `EEA_UK_CH` (narrow EEA/UK/CH collection-regime set — the client-side pre-gate for contacts-store contribution; the server re-gates authoritatively). Country codes come from onboarding / the discovery row, never from a scrape.
- **`core/tz_country.py`** — `timezone_for_country(code)` (pytz `country_timezones`) for the active-hours window.
- **`core/logging.py`** — `configure_logging` + `print_banner`; `SILENCED_LOGGERS` quiets urllib3/httpx/pydantic_ai/openai/fastembed/etc.
- **`core/migration_compat.py`** + **`management/commands/migrate.py`** — relabel `linkedin → legacy` in `django_migrations` before Django's consistency check, so pre-pivot installs upgrade with a plain `migrate`.
- **`contacts/service.py`** — the hub client: `resolve(lead)` (free read before the paid finder; `/resolve` returns an `emails[]` list, first taken), `contribute(session, lead, emails, origin)` (give-back at a fresh paid hit, non-EEA only, registers + mints the token on first use; optionally attaches the cached embedding). Reads `SiteConfig.contacts_api_token`/`contacts_api_url`.

## Configuration

- **`SiteConfig`** (DB singleton) — see CRM Data Model. Editable via Django Admin.
- **`conf.py` schedule** — `ENABLE_ACTIVE_HOURS` (`False` → 24/7), `ACTIVE_START_HOUR` (9), `ACTIVE_END_HOUR` (19), `ACTIVE_TIMEZONE` (`None` → resolved at runtime from the operator's onboarding country via `tz_country`; set an IANA name to pin it). The daemon and scheduler primitives take the resolved zone as an argument and log its provenance. Single contiguous window, no weekend handling.
- **`conf.py:FIND_EMAIL_DAILY_CAP`** (50) — max BetterContact lookups planned per 24h window per campaign (the paid-spend guard; a verified hit costs one credit).
- **`conf.py:DEFAULT_EMAIL_DAILY_LIMIT`** (30) — the per-mailbox warm-safe send ceiling set at onboarding and stored on each `Mailbox` (enforced at send time, per box).
- **`conf.py:CAMPAIGN_CONFIG`** — `min_gp_confidence` (0.9, the GP rank gate), `qualification_n_mc_samples` (100), `embedding_model` (`BAAI/bge-small-en-v1.5`), and the human-rhythm knobs `burst_min/max_seconds` (45–65 min), `break_min/max_seconds` (10–20 min).
- **Prompt templates** (`core/templates/prompts/`) — `icp_filters.j2`, `qualify_lead.j2`, `_outreach_base.j2` (shared base), `email_opener.j2`, `follow_up_agent.j2`.
- **`requirements/`** — `base.txt`, `local.txt`, `production.txt`, `crm.txt` (empty).

## Docker

Multi-stage build from `python:3.12-slim-bookworm` using `uv` (no browser, no VNC).
`compose/linkedin/Dockerfile` (the directory name is historical). `BUILD_ENV` arg selects
requirements; data persists in a volume at `/app/data`.

## CI/CD

- `tests.yml` — pytest on push / PRs.
- `deploy.yml` — on `v*` tags: build + push to `ghcr.io/eracle/openoutreach` (tags `latest`, `sha-<commit>`, semver).

## Dependencies

`requirements/` files; `uv pip install` for fast installs. No browser/Playwright, no DjangoCRM.

Core: `Django`, `pydantic`, `pydantic-ai-slim` (with `openai`/`anthropic`/`google`/`groq`/`mistral`/`cohere`/`bedrock` extras; `griffe` pinned `<2`), `jinja2`, `pandas`, `termcolor`, `tenacity`, `questionary`, `tendo`, `pyyaml`, `jsonpath-ng`
ML: `scikit-learn`, `fastembed`, `huggingface_hub`, `numpy`/`joblib` (transitive)
