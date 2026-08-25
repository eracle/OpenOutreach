![OpenOutreach Logo](docs/logo.png)

# OpenOutreach — open-source AI agent for B2B lead generation

> **Describe your product. Define your target market. The AI finds the people who fit — and tells you why each one does.**
>
> Self-hosted CLI. The output is a CSV your cold-email tool can send.

<div align="center">

[![GitHub stars](https://img.shields.io/github/stars/eracle/OpenOutreach.svg?style=flat-square&logo=github)](https://github.com/eracle/OpenOutreach/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/eracle/OpenOutreach.svg?style=flat-square&logo=github)](https://github.com/eracle/OpenOutreach/network/members)
[![License: GPLv3](https://img.shields.io/badge/License-GPLv3-blue.svg?style=flat-square)](https://www.gnu.org/licenses/gpl-3.0)
[![Open Issues](https://img.shields.io/github/issues/eracle/OpenOutreach.svg?style=flat-square&logo=github)](https://github.com/eracle/OpenOutreach/issues)

<br/>

## Demo

<img src="docs/demo.gif" alt="Demo Animation" width="100%"/>

</div>

---

### 🚀 What is OpenOutreach?

OpenOutreach is a **self-hosted, open-source lead finder that qualifies for you**. You describe your product and your target market; it discovers matching people from a **licensed data provider**, judges each one against the ICP it learned from your description, and hands you the ones that fit — **with the reason each was chosen written out**. You send with whatever you already run.

Two things make that different from what you may have used before:

- **Unlike a cold-email sequencer, you don't bring a list.** There is nothing to upload. The input is a sentence about your product.
- **Unlike a lead database, the output is not rows.** It is a verdict per person, in plain language you can read and disagree with — and correcting the description is how you correct the verdicts.

It has **zero platform-ToS surface**: browserless, no social-network account, no scraping. There is no account to get banned, because there is no account.

**How it works:**

1. **You provide** a product description and a campaign objective (e.g. "SaaS analytics platform" targeting "VP of Engineering at Series B startups")
2. **An LLM turns that into opening search keywords** and pages matching firmographic profiles from a **licensed discovery source** (BetterContact **Lead Finder**) — no emails yet, billed nothing
3. **Discovery walks the keyword index by counting**, adding one word at a time and spending its next query where the accepted-lead counts say the best ones came from. No model, no cadence knob
4. **An LLM qualifies each candidate** against your ICP and **writes down why**. A per-campaign model (Gaussian Process over profile embeddings) learns from those verdicts and picks who to qualify next
5. **The whole campaign prints as CSV when the run ends** — name, title, company, website, profile URL, and the `reason` — so `> leads.csv` is the only file there is. Ask for addresses (`find 10 emails`, or `--emails`) and the best-fit leads get a **paid email lookup** first, one credit per verified hit, gated on the model's confidence so the spend goes to the leads most likely to fit

Searching the licensed source is free, so the system can afford to look at a lot and spend paid lookups only on the best fits. *(The learning loop is an active experiment — it is not yet shown to beat picking at random, and no claim is made that it does.)*

---

## 📤 What You Get Out

The deliverable is a file, and it is shaped for the tools you already send with. You ask for an
amount, and you get it back:

```bash
openoutreach find 10 emails > leads.csv
```

It runs until it has ten more leads carrying an address, prints **the whole campaign** as CSV, and
exits — so the file you just wrote is always the current truth, and there is nothing to poll and
nothing to supervise. Exit 0 means it got what you asked for; anything short still prints its rows
and says why it stopped.

Want to know where things stand without running a job? Ask it:

```bash
openoutreach status            # human summary
openoutreach status --json     # the same thing, for a script or an agent
```

```
email, first_name, last_name, company, title, website, linkedin_url, reason, lead_id, qualified_at
```

Those column names are **the importers', not ours**. Instantly and Smartlead both require `email`/`first_name`/`last_name` and recognise `company`/`title`/`website`/`linkedin_url` as standard fields, so an exported file imports **without column mapping**. Anything else — including `reason` — arrives as a custom variable you can merge into a template.

- **`reason` is the point.** Everybody exports rows; almost nobody exports *why this lead*.
- **There is no score column, on purpose.** The model's confidence is a spend gate for the paid lookup, not a quality signal, and thresholding on it would be reading a number that was never calibrated to mean "good lead". The fit verdict is the LLM's, and it is already in the file as a sentence.
- **A lead with no email still exports.** If you have no email-finder credits, you still get the qualified person, their employer and the reason.
- **A rejected lead never exports.** Both rejections are excluded, always: the LLM's campaign-scoped "wrong fit" and the permanent account-level opt-out.

The export is a **one-way boundary**. Leads leave; nothing comes back. There is no inbound endpoint, no reply vocabulary and no callback to register — whoever sends owns the conversation, the suppression list and the opt-out duty. That is what keeps every integration equal: a sequencer, a CRM and a spreadsheet all read the same rows, and our own sender gets no private door.

> **One thing to do on the receiving side:** turn on your sequencer's *import dedupe*. It is opt-in on Smartlead and undocumented on Instantly, so a lead you export twice can otherwise be contacted twice.

---

### 📭 It does not send email

Earlier versions ran agentic email outreach from a mailbox you owned. That half has been **handed off** to [OpenEmailSequence](https://github.com/eracle/OpenEmailSequence), because sending is a specialism — and a good lead list ruined by a bad opener reads to the buyer as a bad lead list.

What that means for you, concretely:

- **No mailbox to connect.** Setup asks for an LLM key, a lead-data key, and what you sell.
- **Nothing gets sent from your identity.** Including, notably, the promotional campaign for OpenOutreach that older versions sent from your mailbox. It is gone.
- **You send with what you already use.** Instantly, Smartlead, Lemlist, HubSpot, a spreadsheet — anything that reads a CSV. **Turn on your tool's import deduplication**; that is the one thing the split hands to you.

---

**Why choose OpenOutreach?**

- 🧠 **You don't need a list** — describe your product; it finds candidates from licensed data
- 📝 **A stated reason per lead** — read exactly why the agent picked someone, and fix the description when it is wrong
- 🔍 **Nothing decides in the dark** — the ICP, the verdicts and the whole pipeline are on your machine and open to read
- 🛡️ **Zero platform-ToS surface** — browserless, no social-network account, no scraping — nothing to get banned
- 💸 **Pay only for what resolves** — searching is free; a paid lookup is rationed and billed on a verified hit
- 📤 **Exports where you already work** — CSV in the shape the sequencer importers expect
- ⚡ **One-command setup** — `uvx openoutreach find 10`, interactive onboarding, no container required

Every comparable tool that qualifies leads for you is paid SaaS. This one is GPLv3, runs on your machine, and you bring your own provider keys.

---

## 💸 How OpenOutreach Stays Free

**Affiliate links, and that is now the whole of it.** The one paid third-party service the tool relies on — the lead-data provider — is surfaced during onboarding through an affiliate link. Sign up through it and the project may earn a commission, **at no markup to you**. Sign up any other way if you prefer.

Two other funding mechanisms have been **removed**, and it is worth saying so plainly because older versions had them: a promotional campaign for OpenOutreach that was sent from your own mailbox under your own identity, and a "Sent with OpenOutreach" line appended to every message. Both died with the sending leg. See the **[Legal Notice](LEGAL_NOTICE.md)** (§4).

---

## 📋 What You Need

| # | What | Example |
|---|------|---------|
| 1 | **An LLM API key** | OpenAI, Anthropic, or any OpenAI-compatible endpoint |
| 2 | **An email-finder API key** ([BetterContact](https://bettercontact.rocks?fpr=openoutreach)) | **Free account: 40 credits, no card.** Powers **both** discovery (Lead Finder, billed nothing) and enrichment (one credit per verified work email, only with `--emails`) |
| 3 | **A product description + target market** | "We sell cloud cost optimization for DevOps teams at mid-market SaaS companies" |

That's it. **No mailbox**, no social-network account, no spreadsheets, no lead databases, no scraping setup. The BetterContact key is required because it drives discovery *and* enrichment — note the barrier is an **account, not a bill**, since searching the index costs nothing and only resolving an address spends a credit.

The BetterContact link above is an **affiliate link** — signing up through it supports OpenOutreach, at no markup to you.

---

## ⚡ Quick Start

```bash
uvx openoutreach find 10
```

or, if you would rather install it:

```bash
pip install openoutreach && openoutreach find 10
```

The interactive onboarding walks you through the inputs above on first run — four steps: product/objective → LLM key (live-verified) → BetterContact key → your email, country and the legal notice. `find` does it for you if it hasn't happened yet; `openoutreach init` does it deliberately, prints the campaign it created and stops before spending anything. Either way every answer can come from the environment instead (`OPENOUTREACH_*`), which is what makes a headless install possible. Everything lives in `~/.openoutreach/data`, so stopping and starting loses nothing: the number you ask for is *more than you already have*, so running it again continues where it left off. No browser, no daemon manager, no container.

**The three verbs:**

```bash
openoutreach init                # set up the pipeline and the campaign, print it, stop
openoutreach find 10             # ten more qualified leads — free, and cannot spend
openoutreach find 10 --emails    # ...and buy an address for whatever is ready
openoutreach find 10 emails      # ten more *with* a work email (one credit each)
openoutreach find 0              # no work — just print what the campaign already has
openoutreach find 1 --open       # ...and open each new profile in your browser as it lands
openoutreach find 1 --debug      # ...and show the discovery walk's reasoning as it goes
openoutreach status              # what is configured, blocked and counted
```

Running it on a server instead? A Docker image is published to GitHub Container Registry for exactly that — see the **[Docker Guide](./docs/docker.md)**.

---

### 🤖 Use it from Claude Code

This repo ships a **Claude Code plugin**, so you can pull leads without leaving your agent session:

```
/plugin marketplace add eracle/OpenOutreach
/plugin install openoutreach@openoutreach
```

The skill (`skills/find-leads/SKILL.md`) teaches Claude when to run `find`, which flags cost credits
and which cannot, how to read the CSV on stdout, and what each `error: <type>` means. It never buys
an address you did not ask for and never accepts the legal notice for you. Prefer skills to plugins?
Copy `skills/find-leads/` into `~/.claude/skills/` instead.

---

## ⚙️ Local Installation (Development)

For contributors or if you prefer running directly on your machine.

### Prerequisites

- [Git](https://git-scm.com/)
- [Python](https://www.python.org/downloads/) (3.12+)

### 1. Clone & Set Up
```bash
git clone https://github.com/eracle/OpenOutreach.git
cd OpenOutreach

# Install deps, run migrations, and bootstrap CRM
make setup
```

### 2. Find Some Leads

```bash
make find N=10          # or: python manage.py find 10 emails
```
The interactive onboarding prompts for your LLM key, BetterContact key, and campaign details on first run. Fully resumable — the goal is *more than you have*, so stopping and running it again continues rather than restarting.

### 3. Read the Verdicts (CRM Admin)

OpenOutreach includes a full CRM web interface via Django Admin:
```bash
# Create an admin account (first time only)
python manage.py createsuperuser

# Start the web server
make admin
```
Then open:
- **Django Admin:** http://localhost:8000/admin/

Browse Leads, Companies and Deals — every qualification decision, with its reason, is a row you can read.

### 4. Collect the file

The run prints the whole campaign as CSV on **stdout**, so the file is wherever you redirect it —
the tool writes nothing for you and there is no path to go looking for:

```bash
make find N=10 > leads.csv          # or: python manage.py find 0 > leads.csv
```

`find 0` does no work and prints what the campaign already has, which is how you re-export without
spending anything. A row exports as soon as the qualifier accepts it — an email address is an
enrichment on top, never a precondition — so the file can carry rows with a blank `email`.
`openoutreach status` counts both.

---
## ✨ Features

| Feature                            | Description                                                                                                          |
|------------------------------------|----------------------------------------------------------------------------------------------------------------------|
| 🧠 **Autonomous Lead Discovery**   | No contact lists needed — an LLM turns your product + objective into opening keywords, and the walk grows them by counting the words that appear in profiles it already accepted. |
| 📝 **A Reason Per Lead**           | Every qualified lead carries the LLM's written rationale for choosing it. It exports alongside the row, so the tool downstream can merge it — and so you can tell a bad ICP from a bad model. |
| 🔒 **Licensed Discovery**          | Firmographic profiles come from a licensed provider (BetterContact Lead Finder) — no scraping, no browser, no account. |
| 🎯 **Pay Only For What Resolves**  | Search against the licensed source is free; a confidence gate rations the paid lookups, billed only on a verified hit. Cost scales with qualified leads, not with how much you searched. |
| 📤 **Export That Just Imports**    | CSV in the exact column names Instantly and Smartlead expect, so a file imports without column mapping. One record schema, one translation layer, no privileged path for our own sender. |
| 💾 **Built-in CRM**               | Django Admin — browse Leads, Companies and Deals, and read every verdict. Everything is local and everything exports. |
| 🔄 **Stateful Pipeline**          | Tracks deal states in a local DB — fully resumable, nothing scheduled in advance, no queue table.                   |
| ⚡ **One-Command Install**          | `uvx openoutreach find 10` — a Python CLI with interactive onboarding, no browser and no container. A Docker image exists for running it on a server. |
| 🤖 **Built For Agents**            | One bounded call: ask for an amount, get the rows on stdout and an exit code that means *I got what you asked for*. No daemon to supervise, no file to discover, nothing to poll. `--json` gives you the records as JSON Lines and the outcome as one object on stderr. |

---

## 📖 How the Pipeline Works

`find` does one thing at a time until your goal is met, asking the deals what they need — there is no queue table and nothing is scheduled in advance. Each pass walks one ordered list and stops at the first thing it can do, so priority *is* that order:

| # | Step | What it does |
|---|------|-------------|
| 1 | **check a lookup** | Polls an in-flight work-email job: hit → `RESOLVED`, miss → `NO_EMAIL_BETTERCONTACT`, still running → ask again later on the same job. |
| 2 | **rank the pool** | Promotes the qualified leads the model is confident about. |
| 3 | **buy an address** | Free hub-cache hit resolves immediately; otherwise fires a paid provider job and parks the deal at `FINDING_EMAIL`. |
| 4 | **top up** | Discovers and qualifies more leads. |

Only step 3 costs money, and its only gate is whether you configured a provider. **Steps 2 and 4 are ungated on purpose**: searching the index is free and qualifying costs one call against your own LLM key, so there is nothing to ration — and what bounds the paid step is the number you typed, since one credit is one verified address.

The run ends when the goal is met, or when **nothing can advance right now** — every lead is waiting on a lookup that is not due yet, or the search has drained. There is no timeout to configure, because each thing being waited on carries its own.

**Discover → qualify → gate → resolve → export.** One LLM pass turns your campaign into opening search keywords; from there the keyword vocabulary grows by counting the words that appear in profiles the LLM has accepted, and the walk keeps firing the most promising set. Qualification runs the GP + LLM loop over the stored firmographic text and writes the `reason`. The GP confidence gate promotes `QUALIFIED → READY_TO_FIND_EMAIL`, **rationing the paid lookup** so only the best-fit leads cost a credit. A miss ends the deal as `NO_EMAIL_BETTERCONTACT` with a blank outcome (so the labeler skips it — an unfindable address is not a fit signal).

**The qualification loop in detail:**

Discovered profiles are embedded (384-dim FastEmbed vectors) from the licensed firmographic payload. Which profile to evaluate next is a balance-driven choice:

- **When negatives outnumber positives** → **exploit**: pick the profile with highest predicted qualification probability (fill the pipeline with likely positives)
- **Otherwise** → **explore**: pick the profile with highest BALD (Bayesian Active Learning by Disagreement) score (seek the most informative label)

All qualification decisions go through the LLM. The GP model selects which candidate to evaluate next and gates promotion from `QUALIFIED` to `READY_TO_FIND_EMAIL`. Every LLM decision feeds back into the model, making candidate selection progressively smarter. **Only the LLM's fit verdict trains it** — no signal from a send has ever entered that loop, which is why handing sending away costs the model nothing.

**Cold start:** a campaign with no acceptances yet has nothing to fit on, so the ICP is also written out as a handful of **synthetic ideal profiles** and embedded as the model's positives. Each real acceptance retires one of them, so the invented evidence thins out at the rate ground truth replaces it. When the unlabelled pool empties, discovery pages a fresh batch.

Configure behavior via Django Admin (`SiteConfig` + `Campaign`).

---

## 📂 Project Structure

```
├── docs/                             # architecture, configuration, docker, lifecycle, testing
├── openoutreach/                    # single source package; Django apps nested inside
│   ├── __main__.py                  # the `openoutreach` console script — the entry point
│   ├── settings.py                  # Django settings (SQLite at ~/.openoutreach/data/db.sqlite3)
│   ├── core/                        # engine app: the job + cycle, Campaign/SiteConfig,
│   │                                #   LLM factory, onboarding, ML + discovery/qualify
│   │                                #   pipeline, the lead export
│   ├── enrichment/                  # the one paid step: provider client + buy/check lookup
│   ├── crm/                         # Lead + Company + Deal models
│   └── contacts/                    # the shared contacts-store client (the hub)
├── manage.py                         # checkout shim over openoutreach/__main__.py
├── pyproject.toml                   # package metadata, dependencies, console script
├── local.yml                        # Docker Compose — the server deploy only
└── Makefile                         # Shortcuts (setup, find, admin, test)
```

---

## 📚 Documentation

- [Architecture](./docs/architecture.md)
- [Configuration](./docs/configuration.md)
- [Docker Installation](./docs/docker.md)
- [Lead & Deal Lifecycle](./docs/profile_lifecycle.md)
- [Testing](./docs/testing.md)

---

## 💬 Channel

Join for support and discussions:
[Telegram Channel](https://t.me/openoutreach)

---

### 🗓️ Book a Free 15-Minute Call

Got a specific use case, feature request, or questions about setup?

Book a **free 15-minute call** — I'd love to hear your needs and improve the tool based on real feedback.

<div align="center">

[![Book a 15-min call](https://img.shields.io/badge/Book%20a%2015--min%20call-28A745?style=for-the-badge&logo=calendar)](https://www.cal.eu/eracle/15min)

</div>

---

### ❤️ Support OpenOutreach

This project is built in spare time to provide powerful, **free** open-source growth tools. Your sponsorship funds faster updates and keeps it free for everyone.

<div align="center">

[![Sponsor with GitHub](https://img.shields.io/badge/Sponsor-%E2%9D%A4-ff69b4?style=for-the-badge&logo=github)](https://github.com/sponsors/eracle)

<br/>

| Tier        | Monthly | Benefits                                                              |
|-------------|---------|-----------------------------------------------------------------------|
| ☕ Supporter | $5      | Huge thanks + name in README supporters list                          |
| 🚀 Booster  | $25     | All above + priority feature requests + early access to new campaigns |
| 🦸 Hero     | $100    | All above + personal 1-on-1 support + influence roadmap               |
| 💎 Legend   | $500+   | All above + custom feature development + shoutout in releases         |

</div>

---

## ⚖️ License

[GNU GPLv3](https://www.gnu.org/licenses/gpl-3.0) — see [LICENCE.md](LICENCE.md)

---

## 📜 Legal Notice

By using this software you accept the [Legal Notice](LEGAL_NOTICE.md). It covers the third-party services you connect (data provider, email-finder), your responsibilities as data controller under data-protection law, what changes now that the tool no longer sends, automatic newsletter subscription for non-opt-in jurisdictions, the central contacts store, and liability disclaimers.

**Use at your own risk — no liability assumed.**

---

<div align="center">

<a href="https://star-history.com/#eracle/OpenOutreach&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=eracle/OpenOutreach&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=eracle/OpenOutreach&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=eracle/OpenOutreach&type=Date" width="400" />
 </picture>
</a>

**Made with ❤️**

</div>
