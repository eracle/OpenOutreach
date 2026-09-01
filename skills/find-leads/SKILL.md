---
name: find-leads
description: Find qualified B2B leads with OpenOutreach — run `openoutreach find N [emails]`, read the CSV it prints on stdout, and hand the rows to whatever sends. Use when the user wants leads, prospects, an ICP-matched contact list, or asks what a campaign already has. Also covers first-run setup (`openoutreach init`), `openoutreach status`, when a lookup costs money, and the verbs that actually send mail (`send`, `run`) — which you never run unasked.
user-invocable: true
argument-hint: [N] [emails]
---

# Finding leads with OpenOutreach

OpenOutreach is a self-hosted CLI lead finder. You describe a product and a target market once;
each run discovers candidates from a licensed data source, has an LLM judge each one against that
ICP, **writes down why**, and prints every lead it has as CSV on stdout.

It is one bounded command: ask for an amount, get rows, exit. **There is no daemon, no background
job, no file the tool writes for the operator, and nothing to poll.** If you find yourself wanting
to tail a log or wait for something, you have the wrong model of this tool.

**It can also send** — `send` mails what is stored, and `run` is find-then-send in one pass. Both
put mail in strangers' inboxes under the user's own identity, so **never run either one unless the
user asked for mail to go out.** `find` is the default answer to "get me leads"; its deliverable is
a CSV for whatever they already send with.

## Is it installed?

```bash
openoutreach status          # human summary
openoutreach status --json   # the same document, for you to parse
```

If the command is missing, run it through `uvx` instead — `uvx openoutreach ...` — or install it
with `pip install openoutreach`. Inside a checkout of the repo, `python manage.py <verb>` is the
same entry point.

`status` never blocks and never spends. It answers `onboarding` (complete or which
`OPENOUTFIND_*` variables are missing), the counts, the credit balance, anything `blocked`, and a
`next_action` — start there whenever you are unsure what state the user is in. It reports the
finding half; the sending half reports itself when `send` runs.

## Setup, if `status` says onboarding is incomplete

```bash
openoutreach init            # interactive wizard on a TTY; environment otherwise
```

`init` creates the database, asks for whatever this install has not been told, and stops **before
spending anything**. It is one flow over both halves — the finding first, then what only the sending
needs. Every answer can come from the environment instead of a prompt, which is what makes a
headless setup possible:

| Step | Environment variables |
|------|----------------------|
| campaign | `OPENOUTFIND_PRODUCT_DOCS`, `OPENOUTFIND_CAMPAIGN_TARGET` |
| llm | `OPENOUTFIND_AI_MODEL`, `OPENOUTFIND_LLM_API_KEY` |
| bettercontact | `OPENOUTFIND_BETTERCONTACT_API_KEY` |
| account | `OPENOUTFIND_OPERATOR_EMAIL`, `OPENOUTFIND_COUNTRY`, `OPENOUTFIND_ACCEPT_LEGAL_NOTICE` |
| the sender | `OUTSEND_OPERATOR_NAME` (who signs the mail), `OUTSEND_MAILBOX_ADDRESS`, `OUTSEND_MAILBOX_PASSWORD` (the provider's **app password**), optional `OUTSEND_BOOKING_LINK` |

**A variable you export is an answer already given**, and the wizard skips that question rather than
asking for it again. The fields both halves share — what you sell, who for, the model and its key —
are asked once and exported under each child's own name (`OPENOUTFIND_PRODUCT_DOCS` and
`OUTSEND_PRODUCT_DOCS` are the same answer), so there is no second copy to keep in step. On a
machine with no TTY, an incomplete install fails naming every variable that would have completed it.

The product description and target market are pages of prose, so pass them as files rather than
shell-quoted strings — quoting a markdown paragraph on a command line corrupts it quietly:

```bash
openoutreach init --product-docs product.md --target target.md
```

**Never accept the legal notice on the user's behalf.** If `OPENOUTFIND_ACCEPT_LEGAL_NOTICE` is
unset, say so and let them set it; do not export it yourself. The same goes for the mailbox
credentials: ask, never guess.

**`init` is the only verb that asks.** `find` creates the database if it has to, but it never
prompts: given an unconfigured install it stops with `onboarding_incomplete`, naming every variable
that would have satisfied it. So run `init` when the user has not configured anything — it fails
cheaply, before any work.

## The work verb you reach for

```bash
openoutreach find 10                 # ten more qualified leads — free, and cannot spend
openoutreach find 10 --emails        # ...and buy an address for whatever cleared the gate
openoutreach find 10 emails          # ten more *carrying* a verified email (≤10 credits)
openoutreach find 0                  # no work at all — just print what is already there
```

Three things about `N` that are easy to get wrong:

- **`N` is how many *more*, not a total.** A store with 30 leads answers `find 10` by working
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
| `--new` | Print only the rows *this run* produced, instead of every lead in the store. Use this when you are reading stdout into your own context rather than into a file. |
| `--json` | The rows as JSON Lines on stdout (the full record, `profile_text` included); the run's metadata — goal, outcome, `next_action` — as one JSON object on stderr, and nothing else there. Prefer it when you are going to parse. |
| `--batch` | Hold everything until the job ends, then print every lead once — the old, pre-streaming shape. Output is progressive *by default* now (see below); reach for `--batch` only if whatever you are piping into cannot handle a stream — a strict single-document JSON parser, for instance. Not something you need for reading into your own context — that is what `--new` is for. |
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

**stdout carries every lead in the store, not just this run's rows.** The newest file supersedes every
earlier one, and a lead whose address resolved since last time comes back with it filled in. It is
one file to overwrite, never a batch per run — so never append, and never stitch runs together.
Rows arrive progressively by default (what is already stored, immediately, then each new
lead as it resolves) rather than all at once at the end — the total is the same either way, so this
only matters if you are piping into something that cannot take a partial stream, in which case
`--batch` restores the old wait-for-the-end behavior.

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
- Rejected leads never export: neither the LLM's "wrong fit" verdict nor a permanent
  account-level opt-out.
- **`reason` is written for the operator, not the prospect.** It justifies a yes/no —
  third-person and evaluative. Never paste it into a message to the lead.

**The CSV *is* the integration.** Instantly, Smartlead, Lemlist, HubSpot, a spreadsheet — the file
imports as-is, and there is no adapter, webhook or plugin to look for. One thing to tell the
operator when you hand a file on: **turn on their tool's import deduplication**. It is opt-in on
Smartlead and undocumented on Instantly, so a re-exported lead can otherwise be contacted twice.

**`find 0` is the re-emit path** — no work, no spend, prints what is already stored. That is
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
| `bad_config` | A value is set but unusable (e.g. an LLM model id no provider answers to). | Read the message; it names the field. |

Treat a non-zero exit as *partial success with a stated reason*, not as "nothing happened" — the
rows are already on stdout. And never report a failed run to the user as "no leads matched": a
throttled or unauthorised run that reads as an empty result is the worst possible answer, which is
why every failure carries a type.

## Handing the leads on

The export is a one-way boundary: leads leave, nothing comes back. There is no inbound endpoint and
no callback. Whoever sends owns the conversation, the suppression list and the opt-out duty.

So the natural next step after a run is another tool's importer — Instantly, Smartlead, Lemlist, a
CRM, a spreadsheet. **Tell the user to switch on their sequencer's import deduplication**: it is
opt-in on Smartlead and undocumented on Instantly, so a lead exported twice can otherwise be
contacted twice.

## The verbs that send mail

```bash
openoutreach send                    # one pass: open whatever the guards allow right now
openoutreach send 5                  # keep going until five conversations are open
openoutreach run 5                   # find five leads carrying an address, then send
```

**These put real mail in strangers' inboxes, signed with the user's name, from the user's mailbox.**
Run them only when the user has asked for mail to go out, in this session, in so many words. "Find
me some leads" is not that ask, and neither is "set this up".

Three things to know if you do run one:

- **`run` spends.** There is no sending without an address, so `run N` finds in the `emails` unit —
  at most N credits — where a bare `find N` cannot spend at all.
- **`send` has its own clocks.** A sending window, a daily cap and pacing between messages all sit
  between "queued" and "sent", so a pass can legitimately open nothing and still succeed.
- **`run` is `find --json` piped into the sender**, in one process. If the search stops short, what
  it found is still sent — a partial find is not a failed run.

Everything the sender knows about a lead comes from the finder's JSON record, so the outreach
agent's opener is written from `profile_text`, not from `reason`.

## Things not to do

- Don't send mail the user didn't ask for. `find` is the answer to "get me leads"; `send` and `run`
  are answers only to "email them".
- Don't invent a daemon, a scheduler, or a watch loop. Every verb is bounded and exits.
- Don't go looking for an output file. There isn't one unless the user redirected stdout.
- Don't spend credits the user didn't ask for (see *What costs money*).
- Don't parse stderr for data, or expect data on stdout to be anything but the result.
