# Architecture

Detailed module documentation for OpenOutreach. See `CLAUDE.md` for rules and quick reference.

OpenOutreach is a browserless **lead finder**: it learns a campaign's ICP and runs the
funnel — **define ICP → discover → qualify → rank → (optionally) resolve an address →
export** — off licensed data, with no social-network account and no scraping.

**It does not send.** The product's output is a CSV of qualified leads, each carrying the
reason the LLM chose it. Everything downstream of that file — the message, the mailbox, the
sequence, the opt-out — belongs to whatever tool the operator already sends with.

## Project Layout

All source lives in the single `openoutreach/` package; Django apps are nested inside it
(dotted `AppConfig.name`, short labels). Two apps, and that is the whole of it:

```
manage.py
tests/
openoutreach/
  settings.py        # Django settings (SQLite at data/db.sqlite3)
  urls.py
  discovery.py       # Lead Finder client (ICP search + row embedding) — the top of the funnel
  core/              # engine app (label: core) — the cycle, operator lookup,
                     #   Campaign/SiteConfig models, llm.py, conf.py, onboarding,
                     #   ML (qualifier/embeddings), discovery+qualify pipeline,
                     #   the lead export, db/ helpers, geo, management commands
  enrichment/        # the one paid step (not an app — no models) — the BetterContact
                     #   client and the two-step buy/check lookup
  crm/               # app (label: crm) — Lead, Company, Deal
  contacts/          # central contacts-store client (service.py only — no models, not an app)
```

Layering: `core` owns orchestration, the ML/discovery/qualify pipeline and the models;
`enrichment` owns the paid provider call and its pipeline step. `core` imports it only at
wiring points (the cycle's hierarchy).

**What used to be here.** An `emails` app carried Mailbox, SMTP/IMAP, the mail log, the sender,
the three send guards and two pipeline steps; `core` carried the outreach agent and the sending
window; `chat` and `legacy` were model-less apps holding migration history. All of it is gone.
The sending half was **ported to [OpenOutSend](https://github.com/eracle/OpenOutSend)**
(see `cold_outreach/` in that repo, which documents every dangling dependency); the model-less
anchors went with the migration history they anchored, because the cut was clean rather than
upgradeable — see *Migrations* below.

## Entry Flow

`openoutreach/__main__.py:main` — the `openoutreach` console script, and the entry point
(`manage.py` is a shim over it for a checkout). **There is no default verb**: a bare invocation prints
the command list, because the work verb needs a number and picking one would spend the operator's
credits on a guess. A global `--db PATH` (or `--db=PATH`) is stripped from argv before Django
parses it and exported as `OPENOUTREACH_DB`, which `settings.py` reads for the SQLite file (default
`~/.openoutreach/data/db.sqlite3` installed, `data/db.sqlite3` in a checkout); the parent directory
is created if missing.

### The verb surface

Three verbs, chosen rather than accumulated — they are what the two readers actually need:

| verb | |
|:-----|:--|
| `init [--product-docs F] [--target F] [--name N] [--json]` | create the pipeline and the campaign, print what it made, spend nothing. Safe to run twice. |
| `find N [emails] [--emails] [--campaign N] [--new] [--json] [--open] [--debug]` | find that many more, print the campaign as CSV, exit. |
| `status [--json]` | what is configured, blocked, counted, and the next action — including the command that prints the CSV. |

**`--campaign` is optional**: `find` takes the only campaign when there is one and raises a
`bad_config` listing them all when there are several. Ambiguity is an error, never a guess — picking
one would spend the operator's credits on the wrong ICP.

**The CSV is not a verb and not a file the tool writes** — it is the stdout of `find`, so redirecting
the command is what produces it. `export_leads` was deleted as a second way to do one thing, and the
one nobody found. See *The Lead Export* below.

**`--json` belongs on every verb with a result** — `init`, `find` and `status`, which is all of them.
A failure answers in the caller's format too: `--json` turns the `error:` line into
`{"error": {"type", "message"}}` on stderr, so a program never has to parse prose to find out why.

**`reset` was removed rather than kept lean** (2026-08-21). It reset a campaign's discovery walk so
the next run re-seeded from the ICP, with `--all` additionally dropping leads, deals, anchors and
the fitted GP behind a database backup and a confirmation prompt. Two verbs' worth of surface for
something the state directory already does: everything an install writes hangs off one root
(`settings.state_dir()`), so **`rm -rf ~/.openoutreach` followed by `openoutreach find` is the
reset**, and configuration comes back from the environment. What that costs is real and worth
naming: the ICP seed only runs for a campaign with **no** query nodes, so there is now no way to
re-seed a walk *while keeping* its leads and verdicts. Changing the ICP text means starting the
evidence over too.

Others were removed rather than renamed. **`setup_crm`** was an implementation detail with a verb
around it — `find` calls the function directly, so nothing is lost. **`reset_data`** deleted every
lead and deal across every campaign, which `reset` already did with a backup first; both are gone now.

### `find` management command (`management/commands/find.py`)

    openoutreach find 10 [emails] [--emails] [--campaign N] [--new] [--json] [--open] [--debug]

**Finding is free, and the free thing is the default.** A bare `find 10` cannot spend a credit,
however many deals have queued up past the confidence gate; `--emails` permits the lookup and the
`emails` unit implies it. **This was inverted on 2026-08-21** — buying used to be on unless
`--no-emails` turned it off, so a run counting *leads* quietly bought addresses for what an earlier
run left ready, and the docstring called it free. A flag you forget should cost a feature, never
money. The default is `buy_addresses=False` in `run_job` and `run_one_action` too, not just at the
CLI, so no caller can spend by omission.

**The unit is a noun, not a flag**, and that is a budget decision: the provider bills one credit per
verified hit, so `find 10 emails` is capped at ten credits by construction — the number typed is the
budget, in the same unit as the invoice. A `--with-email` modifier would have hidden the money, and
`--max-credits` would be a second ceiling saying the same thing. The noun says what to **count**;
`--emails` says what may be **paid for**. They meet in exactly one place: a goal counted in addresses
cannot be met without buying them, so the unit implies the flag.

`--debug` is the shorthand for `--log-level debug`; both write the same dest, so they cannot disagree. `find 0` does no work and prints
what is already there; it is not a special case, it is `produced >= count` being true before the loop.

Startup sequence, inherited whole from the deleted `run`:
1. **Configure logging** — level from `--verbosity`, banner, noisy third-party loggers silenced (`core/logging.py`).
2. **Ensure DB** — `migrate --no-input` (narrated to stderr; the custom migrate, see below) + the idempotent CRM bootstrap (`core/management/setup_crm.py`, a function — the verb around it is gone).
3. **Onboard** — if `missing_keys()` is non-empty: `hydrate_from_env()` first, then the interactive wizard on a TTY, else exit **naming the variables** that would have satisfied it (no TTY, no silent partial start). See *The environment path* below.
4. **Validate the operator** — `llm_api_key` set, an active operator `User` exists, at least one campaign. All three exit loudly rather than starting work that cannot go anywhere.
5. **Select the campaign** — the named one, or the only one. Several campaigns and no `--campaign` is a typed error listing them; nothing is ever guessed.
6. **Run the job** — `core/job.py:run_job`, then print.

**stdout carries the whole campaign, not just this run's rows.** That is what makes `> leads.csv`
correct by construction: the newest file supersedes every earlier one, and a lead whose address
resolved since last time comes back with it filled in. It is one file you overwrite, not a batch per
run. `--new` narrows to what this run produced — the escape hatch for a caller reading stdout into a
context window rather than into a file — and `--json` emits the rows as JSON Lines, one record per
line, with the goal, the outcome and the next action as one JSON object on stderr.

**The run ends with the one thing to do next, and it derives none of it.** `_report` reads
`build_status()["next_action"]` and renders it with `status.render_next_action` — on stderr beside the
counts, or as the `next_action` key when `--json` is asked for. That matters most on a run that stops
with ranked leads and an empty wallet: the moment to offer a top-up is the moment the run ends, and
nothing else prints it. **Two earlier attempts put the arithmetic in the wrong place** — the balance
read and a deal count inside `core/job.py` gave the bounded-goal loop an HTTP call to a payment
provider, and inside `enrichment/lookup.py` it went per-deal, so *read once* and *ask once* both
needed module-level mutable state. The ask is about the state the run **left behind**, so it is read
once after the work rather than carried through it, and there is one derivation with two callers.

**Exit 0 means the goal was met, and nothing else.** Short of it, the rows still print and one
`error: <type>: <message>` line goes to stderr: *the code says how much you got, the type says why it
stopped*. That is the property that lets a caller branch without parsing anything, and the cost —
`set -e` dying on a good partial result — is real and accepted.

`--open` hands each new lead's `profile_url` to the operator's own browser as it lands. **It does not
spend the browserless claim**: nothing is fetched, automated or authenticated, and `profile_url` stays
*stored, never fetched* by us. No browser available is a `bad_config` error at argument time, because
a flag that silently does nothing is the bug you find at 2am.

Docker's `start` script `exec`s `openoutreach find "$@"` — one job, then the container exits.

### The other two verbs

- `init` — **the phase that already happened, given a name.** Migrate, bootstrap the CRM, onboard, create the campaign, validate the operator — then stop and print what it made. It cannot spend, so it is the one verb always safe to run.
- `status` — **what is in the database, without running a job.** Human summary by default, `--json` for a program. See *Status* below.

## The Output Contract (`core/management/base.py`, `core/errors.py`)

What a program depends on, and the reason both exist as one place rather than a habit.

- **stdout is result-only.** Logs, the startup banner, and Django's `migrate` narration all go to
  **stderr** — `configure_logging` attaches its handler there, `print_banner` writes there, and
  `run` passes `stdout=self.stderr` into `call_command("migrate")`. Verified end-to-end: a
  headless `run` that cannot start writes **zero bytes** to stdout.
- **Colour is gated on `stderr.isatty()`**, not stdout, or piping a result would strip the colour out
  of an interactive run.
- **An expected failure is one line**: `error: <type>: <message>` on stderr, exit 1, no traceback.
  `OpenOutreachCommand.run_from_argv` catches `OpenOutreachError` and writes it verbatim; anything
  else keeps Django's behaviour, because a bug deserves its traceback. `OpenOutreachError` is
  deliberately **not** a `CommandError` — Django prefixes that with the exception's class name, which
  would put noise in front of the line an agent parses.
- **A fresh install is an answer too.** `settings.py` creates the data *directory*, but only `run`
  migrates, so on a wheel every other verb used to die on a raw `no such table: core_campaign`.
  `OpenOutreachCommand.requires_database` (default `True`; `run` sets it `False`) checks for the
  schema in `execute()` — **after** argument parsing, so `--help` still answers — and raises
  `not_initialized`, naming the database path. Reporting zero campaigns instead would be precisely
  the empty-result failure `core/errors.py` exists to prevent: nothing was found because nothing has
  ever run.
- **A failure answers in the caller's format.** `run_from_argv` renders `OpenOutreachError` through
  `format_failure`: the `error: <type>: <message>` line, or `{"error": {"type", "message"}}` when
  `--json` is in argv. **Both on stderr** — a caller that asked for JSON is parsing rather than
  reading, but stdout stays result-only either way, or `find 10 --json > leads.json` would write an
  error object into the file the operator is keeping.
- **Only what a configuration can cause is a credential answer.** `verify_llm_credentials` catches
  the provider's own refusals (`ModelAPIError`), an unusable model id (`UserError`/`ValueError`) —
  and nothing else, because its caller reports whatever it returns as
  `bad_config: OPENOUTREACH_LLM_API_KEY`. It used to catch `Exception`, so when `anthropic` 1.0.0
  dropped `temperature` the resulting `TypeError` in our own process read as a rejected key and sent
  the operator after credentials that were fine. A broken install is a bug and gets its traceback.
- **The provider's refusals are three different things**, typed at the HTTP boundary in
  `bettercontact._request`: 401 → `provider_auth`, 402 → `provider_out_of_credits`, an exhausted 429
  backoff → `provider_rate_limited`. Everything else stays `provider_unavailable`.
- **A 429 is backed off, never retried at speed** — their docs warn that a client which keeps firing
  can get the *account* blocked. The backoff is `urllib3.Retry` mounted on the session
  (`_RETRY`: 5 attempts, `backoff_factor=5` doubling, `backoff_max=120`, `respect_retry_after_header`,
  forcelist `(429,)` only). It lives in the transport rather than in our code because urllib3 already
  implements exactly this, `Retry-After` included; `tenacity` retries on *exceptions*, so using it
  here would mean raising one just to make it fire.

## The Run's Narrative (`core/logging.py`, `core/logblock.py`, and the log lines)

What `find` prints on stderr while it works **is** the product surface: it is what a first-time
operator watches and what an agent summarises for its human. stdout is the CSV and stays result-only.
The script it follows is written down in `openoutreach-docs/docs/first-run-design.md` §6 — the UX
definition came first and the code implements it, rather than the other way round.

**Two levels, and nothing is deleted between them.** INFO is addressed to the operator: every line
answers a question they would ask. DEBUG (`--debug`) is addressed to the maintainer and carries
**everything INFO ever said plus the engine's own reasoning** — the GP's acquisition strategy and
class counts (`qualify.py`), the posterior behind the spend gate (`ready_pool.py`), the frontier, node
retirements and page offsets (`discover.py`), the machine discovery seed and the anchor top-up
(`icp.py`), and the cycle's per-row decisions and timings (`cycle.py`). A line that stops being
operator-facing is **demoted, never removed**: the redesign changed who a line is addressed to, not
what the tool is willing to say.

The beats, in order:

- **Minute 0 states the deal** — campaign, the goal the operator typed, and whether this run may
  spend (`find._announce_the_run`). Spending is opt-in at every layer, which is a good default and an
  invisible one; an operator who expected addresses learns it in the first line, not from an empty
  column at the end.
- **The ICP echo** (`icp.log_icp_echo`) — the synthetic ideal profiles, printed as *"Looking for
  people like:"*. They were already computed, already one line each in `profile_text`'s shape, and
  until now only their *count* was shown. It is the earliest proof the product description was
  understood, and therefore the earliest chance to correct it. A first run has none yet — they are
  written during the job and print themselves there.
- **Both verdicts** (`qualify._verdict_line`) — a rejection carries its reason exactly as an
  acceptance does, `✗` against `✓`. *Watching it turn people down is what makes the acceptances
  credible*; a log of nothing but hits reads like a row dump. `logblock.step_line` is not the
  primitive here: its fixed-width label column aligns short plumbing labels, and a person's name
  overflows it on every row.
- **Progress counts toward the goal** (`job._log_progress`) — `2 of 10 leads · 58 seen · 1m34s`, in
  the unit the operator typed, with `★ first lead · 52s` as its own milestone because *how long until
  anything at all happens* is what a first run is really asking. `JobResult.elapsed` is **reported,
  never enforced** — there is still no timeout.
- **The gate that is actually holding is named** (`cycle.pipeline_summary`) — and **every consequence
  it has**. The finder key is one key doing two jobs, so losing it stops the walk finding anybody
  *and* stops the paid lookup; naming only the second sent the operator after the wrong thing. It
  does not stop the free address sources. `buy_addresses` is passed in because the run holds that
  permission and the summary cannot see it: *addresses not requested* is the gate most likely to be
  holding on a bare `find`.
- **The run ends with the next action** — see the `find` section above.

`logblock.py`'s grammar (a `▶` header naming one action, aligned step lines under it) is the
foundation and was not replaced; the redesign happened inside it.

## Status (`core/status.py`)

`build_status()` assembles one dict and reads nothing else; the command renders it. SQLite runs in
WAL, so it still answers while a job holds a write lock (verified against a held `BEGIN IMMEDIATE`).
One renderer lives in `core/status.py` rather than in the command — `render_next_action` — because
the end of a `find` run prints the same ask, and the sentence must have one spelling.

**It is smaller than it was, on purpose.** It began as the verb an agent asked *instead of tailing a
log*, because a daemon could not answer for itself — `next_action` existed so a caller had something
to interrogate at all. With `find` bounded by a goal the work verb returns its own result, and this
one is back to being what it says: the standing state of the database, for a reader who did not run
the job.

| key | what it carries |
|:----|:----------------|
| `onboarding` | `complete`, the satisfied steps, and per-step the variables that would satisfy the rest |
| `campaigns` / `totals` | the pipeline counts, per campaign and summed |
| `credits` | `balance` + `error` — `GET /api/v2/account` → `credits_left` |
| `hub` | `balance` + `known` — the give-to-get counter (`contacts.service.hub_balance`), a different number on a different service than `credits` |
| `blocked` | what stands between now and more qualified rows, typed from `core/errors.py` |
| `next_action` | the one thing to do next, with what it unlocks and the command or URL that does it |

Three decisions inside it:

- **A balance that could not be read is not a balance of zero.** `no_credential`,
  `provider_auth` and `provider_unavailable` are reported as *why*, because a rejected key must
  never render like a run that has simply found nothing yet.
- **`exportable` is not `mailable`.** The export excludes only the two rejections, so a `QUALIFIED`
  lead exports with a blank `email` column — an address is an enrichment on top, never a
  precondition. The counts therefore split `exportable_with_email` / `exportable_without_email`, and
  they are counted **from the records** rather than from `RESOLVED` standing in for them.
- **`next_action` is ordered by what blocks progress**, so `add_credits` sits above `print_leads`:
  a ranked lead cannot advance without credits, while printing what exists costs nothing. This does
  not break the *never before value* rule — `ranked_for_lookup > 0` is itself the proof that
  qualified leads with written reasons exist, and a campaign that has qualified nobody is asked for
  no money, only told to go and find some (`find_leads`).
- **The hub balance is not the `Credits:` line.** `credits` is BetterContact's own prepaid balance
  (`GET /api/v2/account`); `hub` is the give-to-get counter on the contacts store — a different
  number on a different service, so it gets its own key rather than being folded into or shown
  beside `credits` under one label. Read via `hub_balance()`, which piggybacks on `register` (a
  record-less call is idempotent server-side and already returns `credits`), so checking it spends
  nothing. `known: False` (no token yet, or a hub outage) must render as *unknown*, never as zero —
  the same rule the provider balance already follows.

## Onboarding (`core/onboarding.py`)

Built as an **ordered list of idempotent steps** (`STEPS`). Each `Step` is
`(key, is_done, run, from_env, env_keys)`: `is_done()` reads the DB (never prompts), `run()` collects
what's missing and **persists it the moment it succeeds**, and `from_env()` does the same from the
environment — the third capability, so a step stays the one place that knows its own fields. `onboard_interactive()` runs only the steps
whose `is_done()` is false, in order — so a partial onboarding resumes exactly where it stopped and
a satisfied step is never revisited. There is no end-of-wizard `apply()` that could half-fail; each
step is its own commit point.

```
campaign        product description + target → Campaign row
llm             LLM creds, live-verified via verify_llm_credentials (retries in place on failure)
bettercontact   API key (mandatory — the SAME key powers Lead Finder discovery AND enrichment)
account         your email (contacts key + newsletter target) → country → newsletter (opt-in) → legal (required gate) → operator User + subscribe
```

**Four steps, and it used to be six.** `mailbox` (seven fields, SMTP auth-checked, with its own
retry loop) and `signature` (a sign-off per never-asked box) came out with the sending leg. That
is most of the install path, and the cost was never the typing: behind the mailbox step stood
`LEGAL_NOTICE.md` §4, which told a prospective operator that the tool would send the maintainer's
promotional campaign from their own mailbox. A lead finder was asking someone to accept a sending
liability before showing them a lead.

### The environment path (`hydrate_from_env`)

An agent-driven install has no TTY, so **this is the main path, not a fallback**. `run` boots
`hydrate_from_env()` → re-check `missing_keys()` → wizard **on a TTY**, or exit **naming the variables
that would have satisfied it**. Every field is an `OPENOUTREACH_*` variable:

```
campaign        OPENOUTREACH_PRODUCT_DESCRIPTION, OPENOUTREACH_CAMPAIGN_TARGET  (+ CAMPAIGN_NAME)
llm             OPENOUTREACH_AI_MODEL, OPENOUTREACH_LLM_API_KEY  (+ LLM_API_BASE, required for openai_compatible:*)
bettercontact   OPENOUTREACH_BETTERCONTACT_API_KEY
account         OPENOUTREACH_OPERATOR_EMAIL, OPENOUTREACH_COUNTRY, OPENOUTREACH_ACCEPT_LEGAL_NOTICE  (+ NEWSLETTER)
```

Four rules, each answering a way this could go quietly wrong:

- **All or nothing per step.** A step with only some of its fields set does not hydrate, because a
  half-applied step would leave state the wizard then has to reconcile.
- **A bad value stops; an absent one asks.** `OnboardingEnvError` (a `SystemExit`) carries
  `error: bad_config: <VAR>: <problem>` — falling through to "missing" would print a variable the
  operator has already set.
- **Legal acceptance is never inferred**, and `NEWSLETTER` defaults **off everywhere** — the wizard's
  jurisdiction-aware default is a suggestion to a human; silence in a config file is not consent.
- **The LLM key is still live-verified.** Headless there is nobody to re-ask, so a bad key must fail at
  boot, where the message is readable, not mid-qualification.

- Cancellation is a **single exception**: prompts return `None` on Ctrl+C, and `_required()` turns that into `OnboardingCancelled` at one boundary.
- A failed step re-asks **its own** fields (LLM retries re-verify) — it never rewinds to an earlier step or restarts the wizard. This is what fixed the "onboarding keeps looping back" bug.
- The operator's email **is asked** (the `account` step) and stored as `User.email`. The newsletter subscribes it and the contacts give-back keys the operator by it. It used to need distinguishing from the mailbox `from_address` (the sending identity) and doubled as a BCC target on the operator's own sends; with no sending leg there is one address again. `account`'s `is_done()` requires an active staff `User` with a **non-blank** email, so a blank-email account can't short-circuit the prompt.
- `missing_keys()` returns the keys of unsatisfied steps (`campaign`/`llm`/`bettercontact`/`account`), so `find` knows onboarding is incomplete until every gate passes.
- The newsletter opt-in **default** is jurisdiction-aware (off in GDPR/opt-in countries via `core/geo.is_gdpr_protected`), but an explicit yes always subscribes (lawful consent anywhere). Nothing is persisted in the `account` step until the Legal Notice is accepted.
- The interactive wizard is vendored in `onboarding_wizard.py`: thin `text`/`integer`/`confirm`/`multiline` functions over questionary/prompt_toolkit, each owning its own validation loop and returning a value or `None` (cancel). No external `openoutreach` package dependency.

## Deal State Machine

`crm/models/deal.py:DealState` (OpenOutreach-owned `TextChoices`) is the whole funnel — a lead
is discovered and qualified **without** an email in hand (Lead Finder returns firmographics, not
addresses), so the funnel qualifies first and *optionally* resolves an address after:

```
QUALIFIED ─(GP rank gate)─▶ READY_TO_FIND_EMAIL ──(buy_address)──▶ FINDING_EMAIL ──(check_lookup)──▶ hit:  RESOLVED
 discovered + qualified      ranked, awaiting the      provider job in flight;      miss: NO_EMAIL_BETTERCONTACT
 exportable from here        paid lookup               request_id on the deal
                            (free hub hit → RESOLVED directly, no job)
```

**Every state below `RESOLVED` is terminal, and that is the shape of the product.** The output is
a row in a file, not a conversation: a deal reaches its verdict, optionally gains an address, and
stops. `core/export.py` reads it from there.

- **`QUALIFIED`** — the LLM judged this lead a fit and wrote `reason`. **Exportable from this moment**; an address is an enrichment on top, never a precondition.
- **`READY_TO_FIND_EMAIL`** — passed the **GP confidence gate** (`ready_pool.promote_to_ready` above `min_gp_confidence`); queued for the *paid* lookup (one credit per verified hit). The gate is the paid-lookup **spend** gate and nothing else — it is not a quality score, and is deliberately absent from the export.
- **`FINDING_EMAIL`** — a provider job is in flight; the deal is excluded from the candidate pool (so the next cycle can't re-select it and double-charge) while `check_lookup` polls to termination. The job handle (`lookup_request_id`) and the poll backoff (`lookup_attempt` + `not_before`) live **on the deal**, so an in-flight lookup survives a restart and its wait gates that one row and nothing else.
- **`RESOLVED`** — an address is in hand. Where a fully-enriched deal comes to rest, at no cost, since nothing iterates it.

**The paid lookup is a two-leg async handshake.** `buy_address` resolves free-hub-first (hit → `RESOLVED` with no job/credit), else fires a provider job and parks at `FINDING_EMAIL`; a couldn't-submit (no key / API down) stays `READY_TO_FIND_EMAIL`. `check_lookup` (poll) is then **tri-state**: hit → `RESOLVED` (address given back to the hub); **miss** (job terminated, no address) → `NO_EMAIL_BETTERCONTACT` — its own terminal, with **no reason and blank outcome**, critically not `FAILED+wrong_fit`, which the ML labeler reads as a negative: a lead we simply couldn't reach was still an LLM fit positive and stays label=1; **still running** → chains the next poll with doubled backoff on the same `request_id`, with no deadline and no attempt limit.

`crm/models/deal.py:Outcome` is down to **two** values: `wrong_fit` and `unknown`. The reply
outcomes it used to carry — converted, not_interested, no_budget, has_solution, bad_timing,
unresponsive — described how a *negotiation* went, and a negotiation is the sender's. They left
with `emails/*`, and that retired a live mislabelling: the reply step wrote the real outcome and
moved the deal to `COMPLETED`, not `FAILED`, so `get_labeled_arrays` (which labels every
non-`FAILED` deal **1**) was training a "not interested" reply as a **positive**.

`Lead.disqualified=True` = permanent account-level exclusion (never given a new deal), and it is a
**different column** from `FAILED` — the export filters both, and the first shipped version
filtered only the second. Pre-Deal Lead states are implicit: url-only (a `Lead` row with a null
`embedding`) vs embedded (has an `embedding` + `profile_text`, awaiting qualification).

*(Two legs have been removed over the project's life. The connect leg — `READY_TO_CONNECT`/
`PENDING`/`CONNECTED` — went with the browser channel. The send leg — `READY_TO_EMAIL`,
`EMAILED`, `COMPLETED`, `UNSUBSCRIBED` — went with `emails/*` to OpenOutSend.)*

## The Cycle

**A queue is a status, not a table.** Work is found by asking the deals what they need
(`Deal.objects.filter(state=…)`), so a deal is available because of its own row. Nothing is created
in advance, so nothing can drift, be lost, or need reconciling — and no row's timestamp can gate
anything but itself.

The loop is `core/job.py`, and it is four lines:

```python
while produced(campaign) < goal.count:
    if not run_one_action(campaign):
        break          # every remaining deal is waiting on its own not_before
```

**There is no timeout, deliberately.** Every unit of work already carries its own bound —
`deal.not_before` is the one retry mechanism there is, the lookup poll doubles its own backoff, and
`urllib3.Retry` bounds the 429s. A job-level clock would be a second timeout answering a question the
first one already answers, and answering it worse: a clock knows nothing about *why* it is waiting.
The terminal condition is derived from real state instead — the job ends when **nothing can advance
right now**, which is what `run_one_action` returning `False` already meant.

**Progress is a set, not a subtraction.** A goal counts the leads that *entered* it during this run,
so a lead rejected mid-run cannot silently cancel out one that was found — and `--new` stays exact
for both units, which matters because an `emails` goal is usually satisfied by leads that were
already exportable and merely gained an address, something no timestamp on the row would identify.

**What ends a job short, in the vocabulary of `core/errors.py`:** `goal_unreached` (nothing left to
do, or `Ctrl-C` — the operator's own deadline, since the one case with no natural bound is a campaign
whose leads are all rejected), or `bad_config` when a `HALTING_ERRORS` exception says the model
rejected the request. Provider refusals keep their own types.

*(Two lines are gone from the shape above along with the sending leg: `read_mail_if_due()` and
`refresh_capacities_if_due()`, the only two periodic side-effects the loop ever had.)*

### What replaced, and why

`core/scheduler.py` wrote `Task` rows that were **permission tokens** stamped with a time — "at
14:32 someone may send one email for campaign A" — without saying to whom; the loop took the
earliest-due token and the handler then went and found its own target. When no token was due the
daemon slept **until the earliest token's timestamp**. On 2026-08-05 that put a live install to
sleep for 34 hours: two BetterContact polls had never terminated, their uncapped backoff had
reached 45h30m, they were the only rows in the table, and 55 ready deals with 70 sends of headroom
had no token and therefore were not work. The shape was inherited from the Playwright era, when a
token queue was how access to one browser got serialised; the browser went, the queue outlived it.

Deleted with it: `Task`/`TaskQuerySet`, `core/scheduler.py` (558 lines), `core/quota.py` (172) and
`Campaign.action_fraction`, `core/session.py`, `add_business_hours`, and `emails/tasks/` (598).

### The hierarchy

`run_one_action(campaign)` walks one ordered list and stops at the first thing it can do, so
priority is exactly the order these are written in:

| # | State | Step | Condition |
|---|---|---|---|
| 1 | `FINDING_EMAIL` | `check_lookup` / `reclaim_lookup` | `not_before` elapsed |
| 2 | `QUALIFIED` | `promote_to_ready` | — |
| 3 | `READY_TO_FIND_EMAIL` | `buy_address` | a provider is configured |
| 4 | *(the campaign itself)* | `top_up` | — |

A state that is not listed is terminal, and terminal costs nothing: `RESOLVED`,
`NO_EMAIL_BETTERCONTACT`, `FAILED`.

**There is no spend gate on rows 2 and 4, and that is the shape of the finder.** Both rows used to
share one (`cycle.room_to_send_today`, now deleted): *never resolve an address, and never spend an
LLM call qualifying, for someone there is no room to email today.* That was right while every lead
ended in a send. It was also the single line that made a mailbox-less install produce **nothing** —
with no `Mailbox` rows the pool headroom was 0, the comparison was never true, and discovery and
qualification both stopped, with no error and no log line saying why.

Nothing replaced it, because nothing is left to ration. Discovery is free, qualification costs one
LLM call against a key the operator pays for directly, and the loop is bounded the only way that
matters: **one unit of work per cycle, forever**. Row 3 is the single paid step and asks only
whether there is a provider to pay.

**Two rows are gone with the sending leg** — answering a reply (`EMAILED` + an unanswered reply)
and sending a first email (`READY_TO_EMAIL`, a mailbox free) — along with both periodic
side-effects the loop used to run before every action: the mail pass (IMAP into each box every five
minutes) and the daily warmth re-measure. They live in OpenOutSend now.

Row 2 is the only **per-campaign** step: building a qualifier dominates the cost of using it, so it
scores the whole `QUALIFIED` pool in one pass and drops the model (`qualifier_for`). There is no
`Lead.is_ranked` column — "worth paying for" is what `READY_TO_FIND_EMAIL` already means.
`promote_to_ready` logs the promotion itself, carrying the score that
justified it (`P(f>0.5)=0.997 ≥ 0.75`), and passes `log=False` so the transition is not printed
twice; the score cannot ride in `reason`, which holds the LLM's qualification rationale.

**The walk is also the job's time accounting.** `ROWS` pairs each row with a name, so every action
logs which row fired and how long it took (`[Email Outreach] buy an email address — 2.3s`), and at
`debug` every row logs its decision time even when it declines. The steps log what they *did*;
without this a row that spends twenty seconds deciding it has nothing to do says so nowhere.

When no row fires, `pipeline_summary` prints the counts — and that same line becomes the *reason* the
job stopped short, because the counts separate a drained search from three addresses still on order,
which are a dead end and a reason to run again in an hour. It used to be throttled to one line a
minute (`_log_idle`, `IDLE_LOG_INTERVAL_S`): idle was the normal state of a process that never ended,
so a line per cycle buried everything else. A bounded job stops at the first idle, so it prints once
and the throttle is gone.

**Log vocabulary is the operator's, not the schema's.** A row is named for what happens to a lead
(`find & qualify new leads`, not `top_up`), the idle counts say what each group is waiting *on*
(`60 waiting to be ranked`, not `Qualified=60`), and the one remaining gate is printed as its
consequence (`no finder key, so not buying addresses`, not a boolean). Function and state names
belong in the code and the diagrams; a log line is read by someone asking what is happening to their
pipeline.

**Campaigns no longer take turns.** `_rotate` round-robined them because a process that never ends
has to decide, forever, whose work to do next. A job names its campaign (`--campaign`, or the only
one), so the fairness question has nobody to be fair between and the scheduler that answered it is
gone.

### Steps

Each takes one entity and returns the next `DealState` or `None`; `cycle._apply` then does **one**
`deal.save()`, so a transition and the fields that justify it commit together. Steps are **total**:
they catch the failures they can actually meet and return an explicit state, so the cycle's
`try/except` is a bug backstop, not a retry policy. A step that wants to wait writes
`deal.not_before` — that is the only retry mechanism there is, and it is also what lets a bounded job
end honestly: a row that waits stops being due, so `run_one_action` can return `False` and mean it.
`HALTING_ERRORS` (today `ModelHTTPError`) is the exception: a misconfigured LLM key ends the job with
a typed `bad_config` line rather than being retried forever behind an `alive` log line.

1. **`buy_address`** (`enrichment/lookup.py`) — resolves cheapest-first: an address already on the lead → `RESOLVED`; the free hub cache (`contacts.resolve`) → `RESOLVED`; else `bettercontact.submit` fires a job, stores `lookup_request_id`, and parks at `FINDING_EMAIL`. Couldn't-submit (no key, API down) stays in `READY_TO_FIND_EMAIL` — no credit spent, no handle to poll — but **backs the deal off first** (`_back_off`, the same doubling the poll uses). Without that the row is still due on the next pass, so the same deal is re-picked immediately and forever: noise every few seconds under the daemon, an endless job now that a run stops only when nothing can advance. *Queued* and *due* are different things.
2. **`check_lookup`** (same module) — polls `lookup_request_id` once: hit → `RESOLVED` + hub give-back; miss → `NO_EMAIL_BETTERCONTACT` (no reason, blank outcome); still-running → double `not_before`. **The only terminal outcomes are the provider's own** — no deadline, no attempt limit. Both were tried: past the deadline the leg abandoned the job and reverted the deal, where the buy step bought a *second* job for the same lead, so a provider outage became a hot resubmit loop (418 submits and 4,512 polls in a week for ~40 leads, none terminating). Doubling makes waiting nearly free (a week costs 17 polls) and refuses to mislabel — a timeout is evidence about the provider, not about the lead. The interval rails at `COLLECT_BACKOFF_MAX_S` (a month) only so `datetime` can still express it. A deal parked here with an **empty** `lookup_request_id` has no job and never cost a credit, so row 1 routes it to **`reclaim_lookup`** → `READY_TO_FIND_EMAIL` instead. The row used to `.exclude(lookup_request_id="")` and skip it, which stranded it in a state no other row claims — measured on a live install: two deals stuck for 206 hours.

A third and fourth step stood here — `send_first_email` and `answer_reply` — and both left with
`emails/*`. Note what did **not** follow them: `_store_identity` in `check_lookup` still writes the
`first_name`/`last_name` the provider echoes back with the address, because those are export
columns (a sequencer's `{{first_name}}` merge tag) rather than send machinery.
3. **`top_up`** (`core/pipeline/top_up.py`) — the one step whose queue is a campaign, because a lead nobody has discovered yet has no row to find. One acquisition move per call, chosen by the qualifier's own cold/explore/exploit strategy (unchanged — see **Qualification ML Pipeline**). It used to have a second path for the freemium promo campaign, whose leads were already in the account; that campaign is gone, so every campaign now takes the one path.

### Pacing and capacity — removed with the sending leg

Three guards lived here — **hours** (Mon–Fri 08:00–20:00 in the operator's own timezone),
**rate** (a 3.5–4.5 minute gap between two first emails from one box) and **volume** (a per-box
daily ceiling *measured* from the box's own Sent folder rather than configured). They exist now in
OpenOutSend, and the reasoning is preserved with them, because it was hard-won: receivers
punish rate and volume separately and a recipient reads the *hour*, so no one guard covers the
others.

Nothing replaced them here. A finder emits nothing to pace.

## Opt-out and suppression — it left with the sender, and that is a legal position

This project **has no opt-out mechanism, and needs none**, because it contacts nobody.

What was here: a `List-Unsubscribe` header pointing at a `+unsub` alias of the operator's own
sending address, a visible reply-line in every body, a mailbox scan that caught client-generated
unsubscribes the threaded reader could never see, and an outreach agent with a `suppress` action
for worded requests — all enforced permanently on `Lead.disqualified`, cross-campaign. Every one of
those is a **sending** mechanism, so all of them moved to OpenOutSend with `emails/*`, and
`core.db.leads.suppress_email` went with them.

A finder that never contacts anyone is not the sender under CAN-SPAM / GDPR / CASL, so the duty is
not inherited — it belongs to whatever tool makes contact. Instantly and Smartlead both block a
suppressed address at import.

**Two things survive, and they are not the same thing.**

- **`Lead.disqualified` stays.** It is the permanent, account-level, cross-campaign exclusion that eleven candidate queries already filter and that `core/export.py` filters too. What is gone is the *inbound* path that used to set it from mail we received; nothing writes it automatically any more.
- **The hub store's own suppression is untouched and unrelated.** A person objecting to the shared contacts store is removed store-wide through the hub's endpoint. That obligation arises from contributing vectors and addresses, runs between the data subject and the hub, and involves no sequencer at any point.

**The one duty the split hands to the operator**, which no code here can discharge: turn on the
receiving sequencer's **import deduplication**. Re-exporting a lead who was contacted but never
opted out can otherwise contact them twice. It is opt-in on Smartlead and undocumented on
Instantly, so every adapter's docs must say so.

**What is knowingly given up.** Bounces never come back, so the enrichment leg has no signal that
it is emitting dead addresses — and there is no other source for that. It is the one cost of the
one-way boundary with no substitute, and the one thing that would justify reopening it (as a single
inbound event, not a reply vocabulary). `lead_id` rides in the export as the join key, so the door
stays open at no cost.

## Qualification ML Pipeline

GPR (sklearn, `ConstantKernel * RBF` inside `Pipeline(StandardScaler, GPR)`) with BALD active
learning, over 384-dim FastEmbed embeddings (`BAAI/bge-small-en-v1.5`) stored on `Lead.embedding`;
per-campaign models persisted in `Campaign.model_blob` (joblib, `compress=3`).

1. **Discovery** feeds the pool as **one counted, add-only walk over keyword sets** (`core/pipeline/select.py`, replacing the retired GP-scored maximal walk). A **node** is a set of `(field, token)` `Keyword` rows; its children are itself plus one more token; there is no remove move, because the frontier is global (every unfired child of every fired node, in one pool) so a shallow node's untried siblings stay reachable without one. Firing a node pages Lead Finder for the conjunction into first-touch `Lead`s via `Lead.discovered_by`. `discovery.filters_for(keywords, headcount)` is the only place a node becomes provider JSON, and it is where the index's three operators are chosen between: tokens in the **same field are joined with a space** (words inside one string AND — the walk's narrowing move, and the generator of the best queries measured: `"founder cto"` counts 9,027 at near-perfect precision), different fields AND as separate keys, and the include-list OR stays **unused** because a union reaches one ~10k window where the same values as separate queries reach one each. The campaign's headcount band rides every node unchanged and is never searched. Dedup is `(campaign, token_key)` (`token_key` = sha256 of the sorted `(field, token)` set, a column because no unique constraint spans an M2M); add-only over three fields makes most nodes reachable several ways, so a node is created once and keeps the parent giving the **highest** estimate.

   **A node's value is arithmetic over labels — no model is involved.** `P̂(node) = (a + 2·P̂(parent)) / (a + b + 2)`, where `a`/`b` are the qualified/rejected leads in the store whose `profile_text` contains all of this node's tokens (`select.LabelStore`, loaded once per pass and held in memory — the store is hundreds of rows, so counting a node is a set-containment scan costing microseconds). That is ordinary Laplace smoothing with the prior pointed at the **parent's rate** rather than at 0.5: the parent supplies the level, the child's own counts move it off, and a thin-evidence child stays near its parent instead of swinging to 0 or 1. **The `LabelStore` counts the campaign's anchors as positives**, and that is what makes the cold phase work at all: expansion only offers a token that has shared a *qualified* profile with the node, so a campaign that has never accepted anybody had no qualified profile, could not grow past its one-token seed nodes, fired queries too broad to qualify anyone, and therefore still had no qualified profile — a closed loop in which the seed's own tokens could never be conjoined into the precise query the walk exists to find. The synthetic ideal profiles are written in `profile_text`'s shape, so they tokenize like any lead and say which words describe the people this campaign wants — the same bargain the GP already takes, on the same evidence: `BayesianQualifier` keeps the anchor profiles permanently, so `Campaign.anchor_profiles` is a fixed set once written and this store needs no phase check to read it. They deliberately do **not** feed the vocabulary: an anchor is one flat string with no per-field structure, and splitting it by guess would file `united states` as a job title. Anchors say which words go *together*; only a real lead row says which field a word is searchable in. Selection draws `θ ~ Beta(a + 2·P̂(parent), b + 2·(1 − P̂(parent)))` per frontier node and fires the argmax — Thompson sampling, but the Beta parameters *are* the smoothed estimate, so it is one line with nothing to tune (`select.THOMPSON = False` gives greedy). Width tracks evidence, so an untried node gets tried and a node measured bad three times stops appearing. **The GP no longer selects queries.** Measured head-to-head on ~4,100 parent→child edges with the GP fit on half the labels and every truth measured on the other half, counting wins outright (pearson 0.661 vs 0.450) and the GP adds *nothing* on top of it (0.660); residual anchoring (`P(parent) + λ·GP delta`) is real but worth 0.02 at λ≈0.15 and *worse than doing nothing* at λ=1. The GP remains the qualifier — it is what produces the `a`/`b` this walk counts. There is **no counting-call gate, no phase split, no clause lattice, no maximals, no `EmptyClauseSet`, and no λ**. Per-node state (keyword set, offset, state, `leads_found`, lead count) is inspectable in Django Admin, as is the `Keyword` vocabulary. See the roadmap card `p1-e3-leadfinder-index-semantics-and-query-model-rethink`.

   **Growth is counting, not generation; retirement is a corpus fact, never a model fact.** The vocabulary (`core/pipeline/vocabulary.py`) is simply *the words appearing in profiles the LLM already accepted*, one word per keyword, admitted at **df ≥ 2** over the qualified profiles — a floor that drops 65% of the vocabulary (3,485 → 1,208 tokens on the label store) and loses **zero** good tokens, while removing a singleton tail that is mostly company names and typos and that would otherwise be 56% of the *top* of any embedding-based ranking. It runs every pass: a tokenize-and-count over a few hundred profiles needs no cadence knob and no high-water mark, which is what replaced LLM clause minting (the LLM wrote prose — `Head of Content Strategy` — and every extra word is another AND, so those values were near-empty before being conjoined with anything). A token's **field** is read from the lead-row fields that *are* that axis (`discovery.KEYWORD_SOURCE_FIELDS`, stored per lead in `Lead.source_fields`), keeping the per-field vocabularies nearly disjoint for free; `lead_seniority` is seeded whole from the provider's closed 12-value list and never grown. Expansion offers only tokens that have shared a **qualified** profile with the node (`LabelStore.cooccurring`), which bounds the frontier without a top-K cap and keeps every child a proposition the evidence can speak to. Nothing is ever retired for scoring badly — the qualifier refits constantly and a barren yield is a verdict about a view — so only **emptiness** retires a node, and which kind depends on the offset, because the provider answers `0` for all of them: an empty page at **offset 0** (after one spaced retry) means the index matches nobody → `dead`, and its whole subtree with it, since a superset matches a subset of people; an empty page **below the 10k reach cap** means the vein drained completely → `drained`, subtree pruned too (every match is already a `Lead` here); an empty page **at the cap** means Elasticsearch's `max_result_window`, not the end of the population → `drained` but the **subtree stays**, because adding a token opens a fresh 10k window. The fourth case is not an answer at all: rows empty while `summary.leads_found` is positive is a **transport artifact** (a burst answered a 71-million-lead query with an empty page in 0.0s), and it never retires anything — the old walk wrote those down as "matches nobody", permanently and for every campaign. `search()` returns a `Page(leads, leads_found)` and the count is read **only at offset 0**, because past the end of *any* result set the API reports 0 (at 10,100 for a huge query, at 500 for a 397-row one). **Keyword injection** survives but is now vestigial: `db/leads.create_lead` still embeds a lead as `profile_text + keyword_terms(retrieving node)` while `profile_text` — the LLM qualifier's input — stays clean. Its original job was letting the GP score a never-run query by its keywords; that job is gone, and what keeps it is the vector space itself, since every cached `Lead.embedding` was built this way.

   Each unit of work (`pools._advance`) picks a lead to label by the qualifier's own **explore/exploit** split (`acquisition_mode`, driven by class balance), and that split *is* the whole steering. **Cold phase** (`qualifier.is_cold` — `n_real_positives < ANCHOR_COUNT`, a clock that ends once real acceptances reach `ANCHOR_COUNT` even though the anchors themselves never leave): do **both** moves every pass, one query in and one label out. Rankings here still lean on the anchors' *guess* at the ICP (below), so no observed signal says a label beats a page or the reverse, and any rule that picked one would be a preference dressed as a policy needing a threshold to tune. Interleaving needs none, and it is what the phase wants anyway: discovery is free, so every page opens a region the next label can be picked across. It can't stall — discovery's return is deliberately ignored, so a saturated frontier or a provider outage still leaves a lead to label; only an empty pool ends the pass. *(Discovery itself no longer has a cold phase: the frontier opens on the ICP seed's tokens, every node is scored the same way from day one, and the label store's base rate stands in for the level a root would have supplied — the empty query is never fired, since it matches everyone and its one 10k window is the provider's famous-company head.)* **The cold phase always exploits**: while real positives are still short of `ANCHOR_COUNT`, the campaign's one goal is *more real positives* — reaching that count ends the phase and makes the downstream balance real, though the anchors stay in every fit after that too — and the highest-P lead is the one most like the ideal profile. BALD does the opposite, spending each call on the lead the model is most *confused* about, which with invented positives is the lead least like the ICP; a live run picked four in a row at P≈0.25–0.42 and got veterinary services, cybersecurity education, K-12 tutoring and a metaverse PM against a health-and-wellness ICP. (The balance could not have chosen it anyway: while `is_cold` held, `n_neg > n_pos` was false by construction, so the axis was pinned to BALD for the whole phase.) **Explore** (`neg ≤ pos`, past the cold phase): label the most *informative* lead in the pool (max BALD) with **no gate** — a low-confidence lead is exactly the label that teaches the GP the most, so filtering by confidence here would discard the point of exploring. The GP now ranks on real positives (plus the permanent anchors), so labelling *is* the better move; page a node in only when the pool is empty. **Exploit** (`neg > pos`): spend the LLM call on a lead that will actually convert — the strongest lead clearing `min_gp_confidence` (`consumable_candidates`); if none clears it, there is nothing worth qualifying, so `discover` more instead.

   The gate is the **same constant the promote gate uses**, and it belongs to exploit alone: it is a *spend* gate — "will this LLM call buy an email, or just park at QUALIFIED?" — not an "is this pool promising?" judgment. Explore wants labels, not emails, so it never consults the gate. (The earlier design applied the gate in **both** states and so ran BALD over the confidence-*filtered* set — picking the most-uncertain lead from a bucket it had just stripped of uncertain leads; that incoherence is what the explore/exploit split removes.) Two other bars that *were* judgments both failed earlier and are not to be reintroduced (see `top_up.py`'s module docstring): each compared an **out-of-sample** candidate score against a bar drawn from **in-sample** ones, and a fitted GP never puts those two populations on the same scale. **Measured 2026-07-17**: the pool tops out at 0.327 against a 0.9 gate, so exploit rarely fires until many more labels exist — a lead the LLM accepts meanwhile parks at QUALIFIED unemailed; it did its job by contributing a label.

   **The GP ranks leads; it no longer ranks queries.** One model decides *which lead to label next* (`qualifier.acquisition_scores`) and gates the paid lookup (`min_gp_confidence`, read by `promote_to_ready` and by `pools._advance`'s exploit branch — the same constant, read from config in both, so they cannot drift). *Which query to fetch next* is counted, not modelled (`select.py`). The unification the keyword injection used to buy was measured and did not hold: over bare keyword strings the GP's posterior collapses toward its prior mean (median +0.080 against +0.797 for real profiles, because a keyword string sits far from every profile embedding), so no absolute threshold on it is meaningful and its ranking of single tokens is topped by df=1 company names. `min_gp_confidence` is *only* the spend gate on the paid lookup. See the roadmap card `p1-e3-leadfinder-index-semantics-and-query-model-rethink` §13.
2. **Balance-driven selection** — `n_negatives > n_positives` → exploit (highest P); else → explore (highest BALD). Anchors count as positives here, but the balance decides nothing while any of them stand — the cold phase is pinned to exploit (above); both run against a real posterior from the first pass. If `acquisition_scores` still returns None the campaign is *unanchored* (LLM outage, no ICP text) — the degraded path, where selection falls back to `creation_date` order because nothing can rank.
3. **LLM decision** — every qualify decision is an LLM call (`qualify_lead.j2` reading the lead's stored `profile_text`); the GP is used only for candidate selection and the confidence gate.
4. **Rank gate** — `ready_pool.promote_to_ready` promotes `QUALIFIED → READY_TO_FIND_EMAIL` when `P(f>0.5)` exceeds `min_gp_confidence` (0.9), so a paid credit is only ever spent on a ranked lead.

The GP needs ≥2 labels of **both** classes to fit, and `qualifier_for` warm-starts each campaign's
from `Lead.get_labeled_arrays` where it is needed rather than holding one resident. Every qualifier
is now a `BayesianQualifier` fitted on the operator's own verdicts; the pre-trained `KitQualifier`
(a HuggingFace kit) existed only for the promo campaign, which had no labels of its own.

**The GP trains on the LLM's fit verdict and on nothing else** (`get_labeled_arrays`: label 1 = any
non-`FAILED` deal, label 0 = `FAILED` + `wrong_fit`). No market signal was ever in that loop, which
is why handing sending away cost the model nothing — there was never a reply, an open or a bounce in
its training data. The correction loop is the human: the operator reads `reason` and edits the
product description and the ICP.

**Anchors — the cold-phase positives.** A first run has no positives at all: the LLM rejects
everything until the ICP is right, so the label set is single-class and *nothing* fits. That is
not a degraded model but an absent one — BALD, `P(f>0.5)`, the promote gate and the query
selector all go dark together, and the engine walks its whole cold phase blind. So a campaign
with no real positive is seeded with a few synthetic ones: `icp.generate_anchors` has the LLM
invent `ANCHOR_COUNT` (3) ideal-lead profiles from `product_docs + campaign_target`, written in
the shape `discovery.profile_text_for` produces, embedded (`ensure_anchors`) and handed to the GP
via `BayesianQualifier.set_anchors`. Several rather than one so the positive region is outlined
rather than pinned to a single hallucination.

- **Profiles, not the product text.** The space they must land in is one of *lead* embeddings;
  marketing prose about the product embeds nowhere near a row of firmographics and would anchor
  the model where no candidate lives. They are also embedded **without** query terms (unlike a
  discovered lead, whose retrieving query rides its embedding) — an anchor claims what a good
  lead looks like, not which query to run, and folding the seed's keywords in would have
  discovery score the seed highly on the strength of our own guess.
- **They never become leads.** They exist only as GP observations plus an Admin window
  (`CampaignAdmin.phase`) onto what the model currently believes; no `Lead` or `Deal` row is
  created and nobody is emailed. Paid spend still needs a real acceptance: `promote_to_ready`
  only ever reads `QUALIFIED` deals, and a deal is only `QUALIFIED` because the LLM accepted a
  real lead — an anchor can raise a real lead's score, it can never be the lead.
- **They are permanent — `set_anchors`/`update` never trim them.** The anchors are a guess at the
  ICP; the campaign's own accepted leads are additional evidence, not a replacement for it, so
  they stand alongside real positives for the campaign's whole life rather than being retired as
  ground truth accumulates. `ensure_anchors` still only ever *fills up to* `ANCHOR_COUNT` and never
  invents more once a real positive exists; `stored_anchors` restores the same fixed set on every
  later boot.
- **`is_cold` is a separate clock, not "any anchor still standing."** It is
  `n_real_positives < ANCHOR_COUNT` — not `has_real_positive`, not "is it fitted?": with anchors
  the GP fits from the first pass, so fittedness says nothing about whether the campaign knows
  anything or has only been told what to hope for. The threshold is the same `ANCHOR_COUNT` the
  anchors were sized to, but reading it off the real positives rather than off the anchor count is
  what lets the anchors stay forever without freezing the engine in its cold-phase acquisition
  strategy forever too.
- **Balancing is skipped only while `is_cold` holds.** `_balance` caps the majority at 2× the
  minority; early on, with the positive class still mostly invented, subsampling would throw away
  real rejections to match it — and balancing has nothing useful to do yet regardless. Once real
  positives reach `ANCHOR_COUNT`, balancing takes over even though the anchors keep contributing
  to every fit after that: there is now enough real evidence for the balance to mean something,
  and 3 fixed synthetic positives are a small, constant addition next to a growing real count.
  Their pull stays local to their own neighbourhood (RBF kernel), which is the shape wanted.
- **`select.LabelStore` counts the anchor profiles as qualified permanently**, for the same
  reason the GP keeps them: a campaign that has never accepted anybody has no qualified profile at
  all, so the discovery walk's co-occurrence expansion could never grow past its one-token seed
  nodes without them. Reading `Campaign.anchor_profiles` needs no phase check any more — the set
  is fixed once written, so there's nothing about it that changes with the phase.

## Django Apps

- **`core`** — Engine: `SiteConfig`, `Campaign` models; the cycle, operator lookup, LLM factory, onboarding, the ML/discovery/qualify pipeline, the lead export, geo, the newsletter signup.
- **`crm`** — `Lead` (identity + embedding + email), `Company` (the shared employer row) and `Deal` (`crm/models/lead.py`, `crm/models/company.py`, `crm/models/deal.py`); also defines `DealState` and `Outcome`.
- **`enrichment`** — **not an app** (no models). `bettercontact.py` (paid finder: the two-leg `submit(query)→request_id` + `poll_once(request_id)→PollOutcome`, the shared blocking `submit_and_poll` transport used by discovery, `is_configured`, `BetterContactQuery`/`Result`/`PollOutcome`/`Unavailable`); `lookup.py` (`buy_address`/`check_lookup`/`reclaim_lookup` — one entity in, the next `DealState` or `None` out). It sat under `emails/` while a resolved address existed to be written to; that coupling is exactly what made a mailbox-less install produce nothing.
- **`contacts`** — the central contacts-store client (`service.py`, no models, **not** an installed app) — "the hub" (`hub.openoutreach.app`), logged under the `hub:` prefix. `resolve(lead)` (free read-back before the paid finder) and `contribute(lead, emails, origin)` (give-back, non-EEA only, registers on first use). Both best-effort; an outage or missing token degrades to a no-op.

**Three apps are gone.** `emails` (Mailbox, the mail log, sender, the send guards, two steps) went
to OpenOutSend. `chat` and `legacy` were model-less anchors holding migration history for
pre-pivot installs; the cut was clean rather than upgradeable, so the history they anchored went
with them — see *Migrations*.

## The Mail Log — moved to OpenOutSend

Every message a mailbox emitted or received was a `Message` row keyed on `(mailbox, message_id)`,
written *before* anything decided what it was, with `kind` + `classifier_version` as the reading and
`processed_at IS NULL` as the third state. Three ordered jobs — **sync** (IMAP → rows, the only
network step), **classify** (pure and versioned over stored bytes, so bumping the version *repairs*
history rather than only affecting future mail) and **project** (bounces → `DeliveryEvent`,
opt-outs → suppression) — plus union-find threading over Message-IDs, so a reply carrying only
`In-Reply-To` still reached its deal.

It exists because the record and the interpretation used to be the same object: deciding a message
was uninteresting *was* deleting it. That cost one install two UIDs for good, the only human reply
it ever received, two apologies to a dead address, and a `SendVerdict` table at 0 rows against 590
sends. The separation is the fix, and it moved intact.

`Deal.thread` and `Deal.mailbox` went with it, and so did `Deal.chat_summary` — a conversation
summary is only worth keeping if something is holding a conversation.

## Migrations

**One `0001_initial` per app, and no history before it.** Removing the sending leg took a large slice
of schema with it — the whole `emails` app, two FKs on `Deal`, four send-era states, the freemium
flag — and the cut was made **clean rather than upgradeable**: the migration history was deleted and
regenerated, and with it went `core/migration_compat.py` and the `migrate` command override that
existed only to reconcile the pre-pivot `linkedin`→`legacy` app rename for existing installs.

The consequence is deliberate and is the reason the decision was recorded: **a database created
before this cut cannot upgrade past it.** A pre-cut install has to stay on the `pre-finder-cut` tag
(the last commit where sending works) — pulling `main` onto it leaves a database that cannot migrate
onto the new `0001_initial`, so the process will not start. There is no upgrade path and none is
planned: export what you need and start fresh. A fresh install migrates from nothing and is
unaffected.

## The Lead Export (`core/export.py`)

The finder's **public output** — what leaves OpenOutreach and reaches whatever the operator
actually sends with. Tier 0 of the integration surface described by the boundary card
(`roadmap/p1-e3-leadfinder-sequencer-boundary.md` in `openoutreach-docs`), whose governing rule
is that **our own sender gets no privileged path**: a sequencer, a CRM and a spreadsheet all
read the same rows.

**The column names are other people's, not ours.** Instantly and Smartlead both *require*
`email`, `first_name`, `last_name`, and both recognise `company`, `title`, `website` and
`linkedin_url` as standard fields, mapping anything else to a custom variable. So the record
uses those names exactly — `company`, not `company_name`; `title`, not `job_title` — and an
exported file imports without column mapping. The internal model keeps its own names
(`profile_url` is provider-agnostic on purpose; a `domain` is not a `website`), and
`lead_record()` is the one translation between the two. That is deliberate: one schema cannot
match N importers, so the mapping is a function, not a migration.

```
email, first_name, last_name, company, title, website, linkedin_url, reason, lead_id, qualified_at
```

`reason` lands as a custom variable and is the reason the product exists. Two columns are there for
us: `lead_id`, a stable join key that survives an address changing under us, and `qualified_at`
(`Deal.creation_date`, ISO 8601), so a file carries its own provenance — a caller tells which rows
its own call produced by comparing against the time it started, and a sequencer imports only what is
newer than last time. **A `new` flag would have been the obvious alternative and is wrong**:
invocation-relative state written into a file that outlives the invocation means two files disagree
about the same lead, and the column is a lie the second time anyone reads it. A timestamp is true
forever.

**The boundary is one-way — nothing comes back** (decided 2026-08-19 on the same card). Reply
outcomes are conversation states that depend on the message and the sender's skill, which is the
half being handed away; ingesting them would infer "was this a good lead" from "did that email
work". Suppression stays with the sender too: a finder that never contacts anyone is not the sender
under CAN-SPAM/GDPR, and the mainstream sequencers block a suppressed address at import
([Instantly](https://help.instantly.ai/en/articles/6192983-global-blocklist),
[Smartlead](https://helpcenter.smartlead.ai/en/articles/139-what-is-global-block-list-your-comprehensive-cold-outreach-guide)).
So there is **no inbound endpoint and no event vocabulary**, and the GP keeps training on the LLM's
fit verdict alone — `get_labeled_arrays` has never seen a market signal. `lead_id` keeps the door
open at no cost if that is ever revisited; the one candidate is `bounced`, which grades the row we
emitted rather than the message. **Note the operator-side duty this creates**: import dedupe is
opt-in on Smartlead and undocumented on Instantly, so a re-exported lead who never opted out can be
contacted twice unless the operator enables it — say so in every adapter's docs.

- **One record, two serialisations.** `JSON_FIELDS` is the record — the ten columns above plus
  `profile_text`, the raw firmographic string the qualifier judged on. `RECORD_FIELDS` is the
  **importer-shaped projection** of it, and `write_csv` projects (`extrasaction="ignore"`) rather
  than defining a second schema, so a field added for a sender never becomes a column an importer
  has to map. `None` writes as an empty cell, which is what an importer expects for a field we
  were never told; `profile_text` is `""`, never absent, so a receiver keying on it need not tell
  *no text* from *no such key*.

  ```
  openoutreach find 50 --json | outsend       # the full record, profile text included
  openoutreach find 50 > leads.csv            # the importer-shaped projection of it
  ```

  `--json` is **JSON Lines** (`write_json_lines`): one record per line and nothing else on stdout,
  with the run's metadata (`campaign`, `goal`, `produced`, `reached`, `stopped_because`,
  `next_action`, `rows`) as one JSON object on **stderr** — so one rule serves both formats,
  *stdout is records, stderr is narration*. Line-delimited because **a truncated stream stays
  usable**: one big object that stops halfway is a parse error and the whole batch is lost, where
  a line-delimited one has already delivered every complete record before the break, and the rest
  is a re-run. Under `--json` stderr carries JSON and nothing else — no banner, no log lines (the
  run object, plus the `{"error": …}` object after it if the run fell short), or every caller ends
  up writing the same fragile `tail -1` over a stream of prose. It does **not** make `find`
  incremental: `_report` still runs after the job and materialises the campaign at the end.
- **`reason` is operator-facing.** It is the `QualificationDecision`'s justification for a yes/no —
  third-person, evaluative, about the act of selecting someone. It is evidence for the person
  running this, **never text for the person receiving the mail**; a sender writes the message from
  `profile_text` instead.
- **The compatibility rule is the substitute for the shared record-schema package we are not
  building**: a receiver ignores keys it does not know, and this side never renames a key or
  repurposes one — it only ever adds. The docs are the contract, so a third party reading JSON
  Lines needs no package from us; two repos and one record cannot stay in step on good intentions.
- **There is no score column, and the export is a pure database read.** An earlier version
  exported the GP's `P(f>0.5)`; it was removed as a category error. `core/pipeline/ready_pool.py`
  defines `min_gp_confidence` as "the paid-lookup spend gate **and nothing else**" — the GP decides
  whether to spend a credit resolving an address, not whether a lead fits. The fit verdict is the
  LLM's and it is already in the file as `reason`, in language a person reads; and since every
  exported lead has a Deal, it has already passed the qualifier, so the number separated nothing.
  It was also expensive and unsafe: scoring meant `qualifier_for`, an O(n³) fit over every label
  (**minutes** on the live install's 2,538-deal campaign, against a docstring assuming "tens to low
  hundreds"), which also calls `ensure_anchors` — so a cold campaign would have made **LLM calls and
  mutated campaign state from a read-only export**. `lead_records` now streams one indexed query
  straight to the writer.
- **The Deal is the unit, not the Lead** — the qualification `reason` is per-campaign, and the
  same person can be a lead in two campaigns with two different verdicts.
- **A Deal is not an endorsement**, and this is the trap the live install exposed. There are *two*
  rejections and they live in different columns: `DealState.FAILED` (+ `wrong_fit`) is the LLM's
  own campaign-scoped rejection, and `Lead.disqualified` is the permanent account-level exclusion
  (an opt-out). The first shipped version filtered only on `disqualified`, so it exported **1,944
  rows** from a campaign where most deals were rejections — rows whose `reason` read *"does not
  align well with the target market"*. Both are now excluded, always; there is no flag to include
  them.
- **No command, no file, and no options — it is the stdout of `find`.** This one took two attempts on
  one day and the history is the argument. `export_leads --campaign N > leads.csv` was a command
  nobody discovered, so it became a CSV the daemon wrote and kept current under `<data dir>/leads/`;
  that needed a naming rule, a collision tie-break, rewrite-vs-append, and an atomic
  temp-file-and-rename. **Every one of those problems existed only because a background process can
  change a row after it was written.** Bounding the run to a goal deleted all of them at once: the
  rows are final when the job ends, so they are simply printed, and the shell does files. What is
  printed is the **whole campaign**, which is what makes `> leads.csv` correct by construction —
  the newest file supersedes every earlier one, and a lead whose address resolved since last time
  comes back with it filled in. It is one file to overwrite, not a batch per run. `find 0` prints
  without working, which covers *give me that again* at zero cost. *(Also deleted with the command:
  the format switch, output path, state filter and rejected-leads escape hatch it never grew.)*

## CRM Data Model

- **SiteConfig** (`core/models.py`) — Singleton (pk=1). `ai_model` (pydantic-ai `provider:model`; valid providers openai/anthropic/google/groq/mistral/cohere/openai_compatible), `llm_api_key`, `llm_api_base` (only for `openai_compatible:*`), `bettercontact_api_key` (blank disables discovery + enrichment), `contacts_api_token`/`contacts_api_url` (token earned on first contribution; blank URL → default hub), `country_code` (ISO-3166 alpha-2 — the only persisted operator setting; drives the email-jurisdiction rules via `core/geo`). `SiteConfig.load()`; `core/llm.get_llm_model()` turns it into a `pydantic_ai.models.Model`.
- **Campaign** (`core/models.py`) — `name` (unique), `users` (M2M to `User`), `product_docs`, `campaign_target`, `model_blob` (per-campaign GP), `country_code` (the ICP's target country, stamped on discovered leads for the geo-gate), `headcount_min`/`headcount_max` (**the ICP size band** — a fixed constraint riding every discovery query unchanged and never a search axis, since loosening a bound queries off-ICP and the provider fills a half-open band with any-size companies rather than returning nothing; a column rather than keywords because it is a *number* the provider takes as a bare scalar). All set by `icp.generate_seed` on cold start. Discovery state lives in `QueryNode` rows, not on the campaign. *(Dropped with the sending leg: `booking_link` — a meeting URL only the outreach agent's prompt ever read — and `is_freemium`/`seed_public_ids`, which marked and seeded the promo campaign.)*
- **Keyword** (`core/models.py`) — one `(field, token)` pair (`lead_job_title = cto`), globally unique and shared across campaigns. A **single word**, never a phrase: every extra word in a Lead Finder value is another AND (`Manager` → `Content Manager` is a ~300× narrowing), so the multi-word values the old pool held were near-empty before being conjoined with anything. Joining is still how the walk narrows, but it happens at query time against measured feedback, one token per move. `field` is constrained to `discovery.SEARCH_FIELDS`; `token` is deliberately unconstrained (outside `lead_seniority` these are free-text search terms and a token the index lacks is just an empty page). `Keyword.rows_for(pairs)` is the one place rows are minted (get-or-create, idempotent).
- **QueryNode** (`core/models.py`) — one node in the walk: `campaign` FK, `keywords` (M2M) + `token_key` (sha256 of the sorted set, the dedup key), `parent` (self-FK — **the level, not provenance**: a child inherits its parent's measured rate as the prior its own counts move off), `next_offset`, `state` (`frontier` / `fired` / `drained` / `dead`), `leads_found` (the provider's corpus count at offset 0, diagnostic only). Unique on `(campaign, token_key)`. **No value column** — the estimate is counted from the label store every time it is needed (`select.estimate`), so there is no counter to drift, nothing to migrate, and nothing to reconcile after a crash; it is also the *same* estimator before and after firing, which is what makes a bad page self-correcting (a node that looked good from the store and returned nobody useful has its own misses land in the counters that made it look good). `pairs` renders the sorted `(field, token)` tuples; `to_filters()` maps onto provider JSON. *(Replaces `Clause`, `DiscoveryQuery` and `EmptyClauseSet`, all dropped in `0013`/`0014`. The anti-monotone prune survives without a blacklist table: a child is skipped at creation if any `dead` node's keyword set is a subset of it — which is the half of the prune that still works once dedup makes the lattice a DAG rather than a tree.)*
- **Company** (`crm/models/company.py`) — the employer, stored once and shared by every `Lead` at that firm. Identity is `key` (unique): the lowercased `domain`, or `name:<lowercased name>` when the provider reported no domain — a single computed column, because no constraint can express "the domain when there is one, the name otherwise". `name`/`domain` are nullable; `from_row(name, domain)` get-or-creates and returns `None` when the row named no company. **What the provider said, not verified truth**: Lead Finder fuzzy-matches this record (a boutique law firm's founder comes back as Meta — see `discovery.TEXT_FIELDS`), so anything treating a Company as an *account* inherits that error. Known limitation of the simple key: a firm seen once with a domain and once without produces two rows (`acme.com` and `name:acme`); reconciling them is a later pass, not merge logic at write time.
- **Lead** (`crm/models/lead.py`) — Keyed on `profile_url` (unique — the discovery provider's per-person URL, the opaque identity/lookup key, **stored, never fetched**). `country_code` (stamped from the discovery ICP; drives the contacts-store geo-gate; blank → never contributed). `embedding` (384-dim float32 BinaryField, built at discovery). `profile_text` (the firmographic text — headline/location/industry/title/company/company-description, plus seniority, company-industry, location state+country, and company-keywords folded in *when the row carries them* — built from the Lead Finder row at discovery, the LLM qualifier's input; no re-scrape). `email` (the finder result; null = not found/unresolved — populated by the two-leg buy/check lookup or a free hub-cache hit, never on the model itself). `disqualified` (the permanent, account-level exclusion the export filters — nothing sets it automatically now that the inbound opt-out path is gone). **Identity fields** — `full_name`, `first_name`, `last_name`, `job_title` (all nullable, `NULL` = the provider never told us) and `company` (FK). They exist for the **lead export** and for the record the product keeps, and are deliberately kept out of `profile_text` and the embedding: a name carries no ICP signal and would only give the GP noise to learn on. **The two name sources are distinct and neither is a guess** — discovery reports one `contact_full_name`; the paid enrichment response reports the real `contact_first_name`/`contact_last_name`, which `enrichment/lookup.py` writes on a hit. A lead resolved from the free hub cache never reaches that provider, so its name parts stay `NULL` rather than being split in-house, because they feed a sequencer's `{{first_name}}` merge tag where a wrong guess lands in someone's cold email. `to_profile_dict()` → `{lead_id, profile_url}`; `embedding_array` for numpy; `get_labeled_arrays(campaign)` → (X, y) for GP warm start (non-FAILED → 1, FAILED+wrong_fit → 0, other FAILED → skipped). Created browserless via `core/db/leads.create_lead(row, country_code)` — there are no scrape accessors.
- **Deal** (`crm/models/deal.py`) — campaign-scoped (`unique(lead, campaign)`). `state` (`DealState`), `outcome` (`Outcome` — now only `wrong_fit`/`unknown`), `reason` (**the product**: why the LLM chose or rejected this lead, in its own words, and the only fit signal that leaves in the export). `not_before` (**the only schedule a deal carries** — "do not touch this row before this time", written by the lookup backoff, null = always eligible), `lookup_request_id`/`lookup_attempt` (the in-flight paid job and its backoff exponent), `creation_date`, `update_date`.

  *Dropped with the sending leg:* `mailbox` and `thread` (FKs into the `emails` app), `email_subject`, `email_sent_at`, and `chat_summary` — every one of them a fact about a conversation.

**The mail-log models — `Thread`, `Message`, `DeliveryEvent`, `FolderCoverage` — and `Mailbox` are
gone** with the `emails` app. See *The Mail Log* above for what they did and where they went.

## Key Modules

Paths relative to `openoutreach/`.

- **`core/job.py`** — the bounded run: `Goal` (count + unit, a **delta** not a total), `JobResult`, `run_job(campaign, goal, on_new_lead)`, `_unit_ids` (progress as a set, per unit). No timeout, by decision — see **The Cycle**.
- **`core/cycle.py`** — the hierarchy, and no loop at all: `run_one_action` (rows 1–4, first match wins), `_apply` (one save per transition), `pipeline_summary` (also the reason a job stopped short), `HALTING_ERRORS`. *(Gone with the daemon: `run_daemon`, `_rotate`, `CYCLE_SECONDS`, `IDLE_LOG_INTERVAL_S`. Gone with the sending leg: `unanswered_replies` — the follow-up trigger — `room_to_send_today` — the spend gate — and `read_mail_if_due`/`refresh_capacities_if_due`, the two periodic side-effects.)*
- **`core/operator.py`** — who is running this install: `get_active_user()`, `campaigns()` (what `--campaign` picks from), `self_profile()`, `seller_name()`/`seller_full_name()`. Nothing is cached across calls — both reads are one indexed row, and a cache would only let a renamed operator keep signing with the old name until restart. Replaces the browser era's `OperatorSession`, which by the end held nothing session-like: just the Django `User` and whichever campaign the handler was on (now a real FK on the deal).
- **`discovery.py`** — Lead Finder client and the provider contract. `search(filters, limit, offset)` → `Page(leads, leads_found)`: the rows plus the corpus count from `summary.leads_found`, surfaced **only at offset 0** (past the end of *any* result set the API reports 0). `SEARCH_FIELDS` is the three axes a node may add tokens to — `lead_industry` is absent because it is **inert** (a nonsense value returns the identical count to no filter), `lead_function` because it and `lead_department` are one field under two names whose values are ORed (naming both *widens* the query), and `lead_department` because no lead row carries a department, so no vocabulary could ever grow for it. `filters_for(keywords, headcount)` is the only place a node becomes provider JSON (same-field tokens space-joined = AND; different fields = separate keys; the include-list OR deliberately unused). `KEYWORD_SOURCE_FIELDS` maps each axis to the row fields that *are* that axis, and `source_fields_for(row)` stores exactly those on the Lead. `profile_text_for(row)` builds the qualifier's text from `TEXT_FIELDS`; `keyword_terms(keywords)` is what rides the embedding. A field earns its `TEXT_FIELDS` slot by **varying between leads**: the GP ranks the pool's candidates against each other, so a field constant across them adds nothing however accurate. That test excludes the `company_*` free text — Lead Finder staples a fuzzy-matched company record onto every row (a law firm's founder comes back as Meta, mission statement and all; 1–4 distinct records per 100-row page), so `company_description` (59% of the old text) and `company_keywords` (21%) were 80% of every vector at ~zero bits; `contact_location` is absent from every response. **Changing `TEXT_FIELDS` moves the vector space — every `Lead` must be re-embedded**, and the raw rows are not persisted, so in practice that means re-discovering. `embed_query`/`embed_queries` were removed with the GP-scored walk. Shares `submit_and_poll` with `enrichment/bettercontact.py`.
- **`core/pipeline/`** — `icp.py` (the two cold-start priors, same inputs, two shapes — `generate_seed`: one LLM pass → the campaign's opening **keywords** and size band. It is the *only* LLM call discovery makes about queries: with no qualified leads there are no profiles to count words from, so the ICP text is the one available source. The spec's phrases are **split into single-word tokens** (the LLM writes `"Head of Growth"`, which Lead Finder reads as three ANDed tokens — narrow enough to be empty before the walk has learned anything), letting measurement decide which pair is worth conjoining; `generate_anchors`/`ensure_anchors`: the ICP as synthetic ideal *profiles*, embedded as the GP's positives so a campaign whose every verdict is a rejection can fit at all, kept permanently once written), `vocabulary.py` (`tokenize`/`profile_tokens`, `refresh` — grow the keyword table from qualified leads' `source_fields` at df≥2, `seed_seniorities` — the closed 12-value list, `admitted_keywords`), `select.py` (**the selector, and it is arithmetic**: `LabelStore` (token sets + verdicts, loaded once per pass), `estimate`/`_beta_params` (the parent-smoothed rate), `frontier`/`next_node` (one pool, Thompson draw, argmax), `expand` (add-only children over co-occurring tokens, dead-subset pruned), `seed_frontier`, `advance`/`retire`/`_prune_descendants`, `token_key`), `discover.py` (`discover(campaign, qualifier)`: ensure vocabulary + frontier → draw a node → page it → harvest into first-touch `Lead`s with keyword-injected embeddings and expand its children (`_harvest`), or classify the empty page and retire (`_handle_empty`) and try the next node; `qualifier` is accepted and ignored), `qualify.py` (`run_qualification` / `fetch_qualification_candidates` — reads `Lead.profile_text`, no scrape), `ready_pool.py` (GP gate: `promote_to_ready`, `find_ready_candidate`; `min_gp_confidence` is the spend gate **and nothing else**), `top_up.py` (`top_up` — **one** acquisition move per call, the cold/explore/exploit strategy ported verbatim from the old `pools._advance`; `_consumable_candidates` is the exploit gate — see its module docstring. The `while True` that used to wrap it is gone: the cycle is the loop). *(`mint.py` is gone — LLM clause minting was replaced by `vocabulary.py`'s counting. `freemium_pool.py` went with the promo campaign.)*
- **`core/ml/`** — `qualifier.py` (`Qualifier` protocol, `BayesianQualifier`, `qualify_with_llm`, `format_prediction`), `embeddings.py` (`embed_text`/`embed_texts`, cached FastEmbed model). *(`KitQualifier` and `hub.py` — the HuggingFace campaign kit — went with the promo campaign, which had no labels of its own to fit on.)*
- **`core/db/leads.py`** — `create_lead(row, country_code)` (persist one Lead Finder row as an embedded Lead, idempotent), `promote_lead_to_deal`, `disqualify_lead`. *(`suppress_email` left with the sending leg — see* Opt-out and suppression.*)*
- **`core/db/deals.py`** — Deal state ops: `set_profile_state`, the state-pool queries (`get_qualified_profiles`, `get_ready_to_find_email_profiles`), `create_disqualified_deal`. `_STATE_LOG_STYLE` colors the funnel transitions in the log.
*(`core/db/summaries.py` and the vendored mem0 prompt under `core/vendor/` are gone. They built a `Deal.profile_summary` — an LLM fact-extraction over the lead's `profile_text` — for the outreach agent, and had **no consumer left in the finder** once the sending leg went: the qualifier reads `profile_text` directly. **Summarising for a message is the sender's job**, so `profile_text` crosses the boundary raw and the receiver extracts what an opener needs — tuned for the reader who wants it, paid for only for people actually written to, and derived once rather than twice with no rule for which wins.)*
- **`core/newsletter.py`** — `subscribe_to_newsletter`, a plain Brevo form POST for the operator's own address at onboarding. Nothing to do with outreach; it moved out of `emails/` when that app was removed.
- **`core/llm.py`** — `get_llm_model()` factory (reads `SiteConfig`, `split_model_id` parses the provider out of `ai_model`, dispatches to the per-provider builder), `build_llm_model` (from explicit creds), `verify_llm_credentials` (one live ping, tenacity-retried, used by onboarding — it returns only the failures a *configuration* causes and lets anything else propagate, since its caller blames `LLM_API_KEY` for whatever comes back), and `run_agent_sync(coro)` — the sync boundary that drives async pydantic-ai on a dedicated long-lived worker-thread loop (never `Agent.run_sync`, whose anyio portal poisons the caller thread's loop slot; never per-call `asyncio.run`, which closes loops the SDK HTTP clients still reference).
- **`core/geo.py`** — jurisdiction sets + predicates: `is_gdpr_protected` (broad opt-in set, drives the newsletter default) and `is_eea_located` / `EEA_UK_CH` (narrow EEA/UK/CH collection-regime set — the client-side pre-gate for contacts-store contribution; the server re-gates authoritatively). Country codes come from onboarding / the discovery row, never from a scrape.
- **`enrichment/bettercontact.py`** — the provider client. The paid finder is the two-leg `submit(query) → request_id` + `poll_once(request_id) → PollOutcome`, so a run never blocks on a poll; the free Lead Finder index uses the blocking `submit_and_poll` transport from the same module, since `discovery.search` genuinely wants a page back. `is_configured()` reads `SiteConfig.bettercontact_api_key` — one key, two endpoints, and only one of them bills.
- **`enrichment/lookup.py`** — the two pipeline steps, `buy_address` / `check_lookup` / `reclaim_lookup`, plus `_store_identity` (the name parts the provider echoes back with the address) and the backoff helpers. The enrichment query is **URL-only by decision** — the provider accepts name and company and resolves better with them, but the less of a lead's record leaves for a third party the better, and URL-only measures ~42% usable. The docstring says not to widen it without a decision to widen it.
- **`core/business_time.py`** — `business_days_between(start, end)`: whole Mon–Fri days elapsed. It was the agent's only sense of a thread's age; with no agent it is now unused by the pipeline and kept as a small, correct utility. Public holidays are not modelled (per-country data we don't carry).
- **`core/logging.py`** — `configure_logging` + `print_banner`; `SILENCED_LOGGERS` quiets urllib3/httpx/pydantic_ai/openai/fastembed/etc.; `format_elapsed` (`52s` / `4m09s` / `1h04m`) for the milestones.
- **`contacts/service.py`** — the hub client: `resolve(lead)` (free read before the paid finder; `/resolve` returns an `emails[]` list plus `credits` on both the hit and the miss, first email taken — a miss logs *no balance* when `credits <= 0` and *no stored email* otherwise, since a permanent zero must not read like an ordinary miss), `contribute(lead, emails, origin)` (give-back at a fresh paid hit, non-EEA only, registers + mints the token on first use; optionally attaches the cached embedding), `hub_balance()` (the give-to-get counter for `status`, read via a record-less `register` call — idempotent server-side, so checking spends nothing; `known: False` on no token or an outage, never a balance of zero). Reads `SiteConfig.contacts_api_token`/`contacts_api_url`.

## Configuration

- **`SiteConfig`** (DB singleton) — see CRM Data Model. Editable via Django Admin.
- **`conf.py` lookup backoff** — `COLLECT_BACKOFF_BASE_S` (5), `COLLECT_BACKOFF_MAX_S` (30 days): the poll doubles its delay on every still-running attempt and **never gives up** — MAX rails the *interval* so `datetime` can still express it, and is not a deadline. **There is no spend cap.** Paid spend used to be gated by mailbox send-headroom (never resolve an address there is no room to email today); with no sending leg that gate is gone and nothing replaced it, because what bounds the spend is the operator's own prepaid credit balance, which the provider enforces and we cannot see. `COLLECT_TODAY_HORIZON_S` went with the gate it served.
- **`conf.py` — the three send guards are gone.** `WARM_*` (the measured per-box daily ceiling), `MIN_SEND_INTERVAL_SECONDS`/`SEND_INTERVAL_JITTER_*` (the 3.5–4.5 minute gap between first emails) and `SEND_WINDOW_*` (Mon–Fri 08:00–20:00, operator-local) were most of this file. They moved with the code they governed; the reasoning is preserved in `cold_outreach/README.md` on the OpenOutSend side, because it is worth not re-deriving: receivers punish *rate* and *volume* separately and a recipient reads the *hour*, so no one guard covers the others.
- **`conf.py:CAMPAIGN_CONFIG`** — `min_gp_confidence` (the GP rank gate — **only** a spend gate on the paid lookup; it is not a steering signal and never a quality score), `qualification_n_mc_samples` (100), `embedding_model` (`BAAI/bge-small-en-v1.5`). **There is no discovery cadence knob**: growing the vocabulary used to be an LLM call worth rationing (`mint_every_n_qualified`, removed) and is now a tokenize-and-count that simply runs every pass. The walk's only other constant is the df≥2 admission floor, which lives in `pipeline/vocabulary.py` beside the measurement that set it.
- **Prompt templates** (`core/templates/prompts/`) — `icp_filters.j2` (the cold-start ICP → seed keywords + size band), `anchor_profiles.j2`, `qualify_lead.j2`. *(`outreach_agent.j2` went with the agent; `mint_clauses.j2` with LLM clause minting.)*
- **`pyproject.toml`** — package metadata, dependencies, dev extras, and the `openoutreach` console
  script. Replaces the `requirements/*.txt` files, which are gone.

## Install

**The tool is CLI-first and ships on PyPI**: `uvx openoutreach`, or `pip install openoutreach`. The
console script is `openoutreach/__main__.py:main`; `manage.py` is a shim over it for a checkout.
Everything the install writes hangs off one root — `settings.state_dir()`, the checkout when
`manage.py` sits beside the package and `~/.openoutreach` otherwise, because a wheel's `ROOT_DIR` is
site-packages and no operator's data belongs there. Under it: `data/db.sqlite3` and
`.cache/fastembed`. The model cache is deliberately **not** derived from the database path, so
`--db /tmp/scratch.sqlite3` does not send fastembed off to re-download 65 MB beside a throwaway DB.

**Measured on a cold machine** (2026-08-20, from a local wheel): `uv` resolves and installs in **30 s**
into a **550 MB** venv, and the first embedding fetches the ONNX weights in a further **11 s** / 65 MB
— about **40 s of one-time cost** before any lead work, which is small enough that no pre-warm verb is
needed. The venv's largest pieces are scipy (81 MB), onnxruntime (61 MB), pandas (45 MB) and Django
(41 MB); `botocore` + `fastavro` + `pillow` (~45 MB together) arrive only through the
`pydantic-ai-slim[…,cohere,bedrock]` extras, and are the one obvious lever if that number ever matters.

See the `openoutreach-docs` card `p1-e2-cli-entry-points`.

## Docker — the server deploy, not the install path

Two-stage build from `python:3.12-slim-bookworm`: stage one installs the package into a venv at
`/opt/venv` with `uv`, stage two copies that one directory and carries neither `git` nor `uv`.
No browser, no VNC. `compose/openoutreach/Dockerfile`. It exists for running jobs on a server
(`openoutreach-docs/docs/infrastructure.md` §7) — **development and tests run natively**, there is no
`BUILD_ENV` and no dev extras in the image. `OPENOUTREACH_DB=/app/data/db.sqlite3` names the CRM path
explicitly, since the code no longer sits beside it; `local.yml` mounts `./data` there and nothing else.

## CI/CD

- `tests.yml` — native pytest on push / PRs (Python 3.12, `uv pip install -e ".[dev]"`).
- `deploy.yml` — **on every push to `main`**, and on `v*` tags. Runs the tests, then builds
  + pushes `ghcr.io/eracle/openoutreach`, then fires a `repository-dispatch` (`image-updated`) at
  `eracle/hub.openoutreach.app`. Image tags: `latest` (default branch only), `sha-<commit>`, and
  semver (`v*` tags only).

  **There is no release gate, on either artifact.** Merging to `main` republishes `:latest` *and*
  publishes a new version to PyPI — so code and **schema migrations reach anyone pulling `latest`, or
  installing the package, on merge**. `sha-<commit>` tags only the **pushed tip**: commits buried
  inside a multi-commit push never get their own image, so a migration can go from unpublished to
  live in one push.

- **Every green push to `main` is a PyPI release** (`publish-pypi`): sdist + wheel through trusted
  publishing (OIDC, environment `pypi` — no API token in the repo), with `skip-existing` so a re-run
  is a no-op rather than a red build. **`needs: test` is the entire gate**, which makes `main` the
  release branch in the literal sense.

  **The version is derived, never committed.** `pyproject.toml`'s `version` is the *base*: major and
  minor are declared there by hand, and the patch is `git rev-list --count v0.1.0..HEAD` at publish
  time — monotonic across a base bump (`0.1.20` → `0.2.21`), unique per commit, and requiring no
  bump commit. That is why the job checks out with `fetch-depth: 0`; a shallow clone cannot count.

  **This replaced tag-gated releases on 2026-08-22, and the reason is worth keeping.** `v0.1.0` was
  tagged by hand, then twelve commits of first-run work landed behind it — the run narrative, the
  out-of-credits ask, and three real fixes — while `uvx openoutreach` kept serving the version from
  before all of them. A release nobody has to remember cannot drift. The trade was made knowingly:
  every commit that passes CI is now public and permanent, and a bad merge reaches installers in
  minutes. **Tags no longer publish** — they stay useful as human markers and the image job still
  reads them for semver tags, but a tag on an already-published commit would collide with the
  version that commit already shipped. The `pypi` environment must **not** carry a required
  reviewer, or every push waits on a click.

## Dependencies

`pyproject.toml`; `uv pip install` for fast installs. No browser/Playwright, no DjangoCRM.

Core: `Django`, `pydantic`, `pydantic-ai-slim` (with `openai`/`anthropic`/`google`/`groq`/`mistral`/`cohere`/`bedrock` extras; `griffe` pinned `<2`), `jinja2`, `pandas`, `termcolor`, `tenacity`, `questionary`, `tendo`, `pyyaml`, `jsonpath-ng`
ML: `scikit-learn`, `fastembed`, `huggingface_hub`, `numpy`/`joblib` (transitive)
