---
name: find-leads
description: Find qualified B2B leads with OpenOutreach — run `openoutreach find N [emails]`, read the CSV it prints on stdout, and hand the rows to whatever sends. Use when the user wants leads, prospects, an ICP-matched contact list, or asks what a campaign already has. Also covers first-run setup (`openoutreach init`), `openoutreach status`, and when a lookup costs money.
user-invocable: true
argument-hint: [N] [emails]
---

# Finding leads with OpenOutreach

OpenOutreach is a self-hosted CLI lead finder. You describe a product and a target market once;
each run discovers candidates from a licensed data source, has an LLM judge each one against that
ICP, **writes down why**, and prints the campaign as CSV on stdout.

It is one bounded command: ask for an amount, get rows, exit. **There is no daemon, no background
job, no file the tool writes for the operator, and nothing to poll.** If you find yourself wanting
to tail a log or wait for something, you have the wrong model of this tool.

It does **not** send email. The deliverable is a CSV for whatever the user already sends with.

## Is it installed?

```bash
openoutreach status          # human summary
openoutreach status --json   # the same document, for you to parse
```

If the command is missing, run it through `uvx` instead — `uvx openoutreach ...` — or install it
with `pip install openoutreach`. Inside a checkout of the repo, `python manage.py <verb>` is the
same entry point.

`status` never blocks and never spends. It answers `onboarding` (complete or which
`OPENOUTREACH_*` variables are missing), per-campaign counts, the credit balance, anything
`blocked`, and a `next_action` — start there whenever you are unsure what state the user is in.

## Setup, if `status` says onboarding is incomplete

```bash
openoutreach init            # interactive wizard on a TTY; environment otherwise
```

`init` creates the database and the campaign, prints what it created, and stops **before spending
anything**. Four steps' worth of input, each of which can come from the environment instead of a
prompt (which is what makes a headless setup possible):

| Step | Environment variables |
|------|----------------------|
| campaign | `OPENOUTREACH_PRODUCT_DESCRIPTION`, `OPENOUTREACH_CAMPAIGN_TARGET` |
| llm | `OPENOUTREACH_AI_MODEL`, `OPENOUTREACH_LLM_API_KEY` |
| bettercontact | `OPENOUTREACH_BETTERCONTACT_API_KEY` |
| account | `OPENOUTREACH_OPERATOR_EMAIL`, `OPENOUTREACH_COUNTRY`, `OPENOUTREACH_ACCEPT_LEGAL_NOTICE` |

The product description and target market are pages of prose, so pass them as files rather than
shell-quoted strings — quoting a markdown paragraph on a command line corrupts it quietly:

```bash
openoutreach init --product-docs product.md --target target.md
```

**Never accept the legal notice on the user's behalf.** If `OPENOUTREACH_ACCEPT_LEGAL_NOTICE` is
unset, say so and let them set it; do not export it yourself.

You never need to run `init` first — `find` does the same setup if it hasn't happened — but do run
it when the user has not configured anything, because it fails cheaply and prints the campaign it
built, so a misread product description is caught before any work.

## The one work verb

```bash
openoutreach find 10                 # ten more qualified leads — free, and cannot spend
openoutreach find 10 --emails        # ...and buy an address for whatever cleared the gate
openoutreach find 10 emails          # ten more *carrying* a verified email (≤10 credits)
openoutreach find 0                  # no work at all — just print what the campaign has
```

Three things about `N` that are easy to get wrong:

- **`N` is how many *more*, not a total.** A campaign with 30 leads answers `find 10` by working
  until it has 40. Runs are fully resumable; re-running continues rather than restarting.
- **`find 0` does no work and spends nothing.** It is how you re-export, or answer "what do we
  have?" without running a job.
- **`N` is a budget when the unit is `emails`.** The provider bills one credit per verified hit, so
  `find 10 emails` is capped at ten credits by construction — the number typed is in the same unit
  as the invoice.

### What costs money

Discovery and qualification are free (they cost only the user's own LLM key). **The address lookup
is the only paid step**, and it is opt-in:

- a bare `find N` **cannot** spend a credit, however many leads are queued past the confidence gate;
- `--emails` permits buying for whatever is ready;
- the `emails` unit implies `--emails`, because a goal counted in addresses cannot be met without
  buying them.

**Do not add `emails` or `--emails` unless the user asked for email addresses.** If they said
"find me leads", run the free form and tell them the paid form exists. A lead with no address still
exports — the row carries the person, the company and the reason with a blank `email`.

### Other flags

| Flag | What it does |
|------|--------------|
| `--new` | Print only the rows *this run* produced, instead of the whole campaign. Use this when you are reading stdout into your own context rather than into a file. |
| `--json` | The rows as JSON Lines on stdout (the full record, `profile_text` included); the run's metadata — goal, outcome, `next_action` — as one JSON object on stderr, and nothing else there. Prefer it when you are going to parse. |
| `--campaign NAME` | Required only when the operator has more than one campaign; ambiguity is an error, never a guess. |
| `--debug` | Show the discovery walk's reasoning on stderr. For diagnosing a run that finds nothing. |
| `--open` | Opens each new lead's profile in a browser. **Never pass this** — it is for a human at a terminal, and it errors out headless. |
| `--db PATH` | Work against a SQLite file other than `~/.openoutreach/data/db.sqlite3` (same as `OPENOUTREACH_DB`). Accepted by every verb. |

A run can take a while: each lead is an LLM call, and paid lookups are polled. Give it a generous
timeout rather than a short one plus a retry — a killed run wastes the work, though nothing already
qualified is lost.

## Reading the output

**stdout is result-only; logs, counts and progress go to stderr.** That is the contract that makes
redirection correct:

```bash
openoutreach find 10 > leads.csv
```

**stdout carries the whole campaign, not just this run's rows.** The newest file supersedes every
earlier one, and a lead whose address resolved since last time comes back with it filled in. It is
one file to overwrite, never a batch per run — so never append, and never stitch runs together.

Columns, in this order:

```
email, first_name, last_name, company, title, website, linkedin_url, reason, lead_id, qualified_at
```

- The names are **the importers'**, not OpenOutreach's: Instantly and Smartlead read
  `email`/`first_name`/`last_name` and recognise `company`/`title`/`website`/`linkedin_url`, so the
  file imports without column mapping. Everything else, `reason` included, arrives as a custom
  variable. **Do not rename these columns** when handing the file on.
- **`reason` is the point** — the LLM's written rationale for choosing this person. It is prose,
  so it contains commas and quotes: parse with a real CSV reader (Python's `csv`, `pandas`), never
  by splitting on `,`. When summarising leads for the user, quote the reason; that is what
  distinguishes these rows from a list bought anywhere else.
- **There is no score column, on purpose.** The model's confidence is a spend gate for the paid
  lookup, not a quality signal — do not go looking for one, and do not synthesise one.
- `lead_id` is the stable key for dedupe across exports. `qualified_at` is when the verdict landed.
- Rejected leads never export: neither the LLM's campaign-scoped "wrong fit" nor a permanent
  account-level opt-out.
- **`reason` is written for the operator, not the prospect.** It justifies a yes/no —
  third-person and evaluative. Never paste it into a message to the lead.

**The CSV *is* the integration.** Instantly, Smartlead, Lemlist, HubSpot, a spreadsheet — the file
imports as-is, and there is no adapter, webhook or plugin to look for. One thing to tell the
operator when you hand a file on: **turn on their tool's import deduplication**. It is opt-in on
Smartlead and undocumented on Instantly, so a re-exported lead can otherwise be contacted twice.

**`find 0` is the re-emit path** — no work, no spend, prints what the campaign already has. That is
what to run for *give me that file again*; there is no `export` verb and none is coming.

### The JSON record

For anything programmatic, prefer `--json` over parsing the CSV. It is **JSON Lines**: one record
per line on stdout, carrying every CSV column plus `profile_text` — the raw firmographic text the
qualifier judged on, which is what a sender writes a message from. The run's own metadata is one
JSON object on stderr:

```bash
openoutreach find 10 --json > leads.jsonl 2> run.json
openoutreach find 10 --json | jq -r '.email'          # the records
```

One record, two serialisations: the JSON is the whole thing, the CSV is the importer-safe
projection of it. A reader **ignores keys it does not know** — the finder never renames a key or
repurposes one, and only ever adds.

## Exit codes and failures

**Exit 0 means the goal was met, and nothing else.** Anything short still prints its rows and exits
non-zero with a single line on stderr:

```
error: <type>: <message>
```

Under `--json` that becomes `{"error": {"type": ..., "message": ...}}`, still on stderr. The `type`
is a stable string worth branching on:

| type | What it means | What to do |
|------|---------------|-----------|
| `goal_unreached` | Ran, produced fewer than asked. The rows are on stdout. | Read the message: a drained index is a dead end, addresses on order are a reason to run again later. |
| `not_initialized` | No pipeline at this database yet. | `openoutreach init` |
| `onboarding_incomplete` | Missing configuration and no TTY to ask. | `openoutreach status` names the variables. |
| `no_credential` | No BetterContact key. | Configure one; discovery needs it too, and the free tier has 40 credits. |
| `provider_auth` | The key was rejected. | Do not retry; the key is wrong. |
| `provider_out_of_credits` | Credits exhausted. | Free `find N` still works; addresses do not. |
| `provider_rate_limited` | 429. | Back off. **Never retry at speed** — the provider's docs say that can block the account. |
| `provider_unavailable` | Provider unreachable at all. | Transient; retry later. |
| `bad_config` | A value is set but unusable (e.g. an unknown `--campaign`). | Read the message; it names the field. |

Treat a non-zero exit as *partial success with a stated reason*, not as "nothing happened" — the
rows are already on stdout. And never report a failed run to the user as "no leads matched": a
throttled or unauthorised run that reads as an empty result is the worst possible answer, which is
why every failure carries a type.

## Handing the leads on

The export is a one-way boundary: leads leave, nothing comes back. There is no inbound endpoint and
no callback. Whoever sends owns the conversation, the suppression list and the opt-out duty.

So the natural next step after a run is another tool's importer — Instantly, Smartlead, Lemlist, a
CRM, a spreadsheet, or [OpenEmailSequence](https://github.com/eracle/OpenEmailSequence) for the
sending half. **Tell the user to switch on their sequencer's import deduplication**: it is opt-in on
Smartlead and undocumented on Instantly, so a lead exported twice can otherwise be contacted twice.

## Things not to do

- Don't invent a daemon, a `run` verb, a scheduler, or a watch loop. `find` is the whole of it.
- Don't go looking for an output file. There isn't one unless the user redirected stdout.
- Don't spend credits the user didn't ask for (see *What costs money*).
- Don't parse stderr for data, or expect data on stdout to be anything but the result.
