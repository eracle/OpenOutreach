![OpenOutreach Logo](docs/logo.png)

> **Describe your product. Define your target market. The AI finds the leads and emails them for you.**

<div align="center">

[![GitHub stars](https://img.shields.io/github/stars/eracle/OpenOutreach.svg?style=flat-square&logo=github)](https://github.com/eracle/OpenOutreach/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/eracle/OpenOutreach.svg?style=flat-square&logo=github)](https://github.com/eracle/OpenOutreach/network/members)
[![License: GPLv3](https://img.shields.io/badge/License-GPLv3-blue.svg?style=flat-square)](https://www.gnu.org/licenses/gpl-3.0)
[![Open Issues](https://img.shields.io/github/issues/eracle/OpenOutreach.svg?style=flat-square&logo=github)](https://github.com/eracle/OpenOutreach/issues)

<br/>

# Demo:

<img src="docs/demo.gif" alt="Demo Animation" width="100%"/>

</div>

---

### 🚀 What is OpenOutreach?

OpenOutreach is a **self-hosted, open-source, email-first AI sales agent** for B2B lead generation. It discovers leads from a **licensed data provider**, qualifies them on your own machine, and runs **agentic email outreach from a mailbox you own** — with **zero platform-ToS surface**: it is browserless, uses no social-network account, and does no scraping. Unlike other tools, **you don't need a list of profiles to contact** — you describe your product and your target market, and the system autonomously discovers, qualifies, and emails the right people.

**How it works:**

1. **You provide** a product description and a campaign objective (e.g. "SaaS analytics platform" targeting "VP of Engineering at Series B startups")
2. **An LLM turns that into an ICP filter** and pages matching firmographic profiles from a **licensed discovery source** (BetterContact **Lead Finder**) — no emails yet, billed nothing
3. **A Bayesian ML model** (Gaussian Process Regressor on profile embeddings) learns which profiles match your ideal customer — an explore/exploit strategy balancing finding the best leads now vs. learning what makes a good lead
4. **An LLM classifies** each candidate the model selects; the GP learns from every decision to pick better candidates over time
5. **Only the best-fit leads get a paid email lookup.** A confidence gate rations a work-email resolution (one credit per verified hit); a hit routes the lead into **agentic email** — an AI agent sends a personalized opener from your mailbox, then reads replies and runs multi-turn follow-up

The system gets smarter with every decision: it explores broadly, then progressively focuses on the highest-value profiles as it learns your ideal customer profile from its own classification history.

**Why choose OpenOutreach?**

- 🧠 **Autonomous lead discovery** — No contact lists needed; AI finds your ideal customers from licensed data
- 📧 **Email-first outreach** — Resolves a work email per qualified lead and sends from **your own mailbox**, at email volume
- 🛡️ **Zero platform-ToS surface** — Browserless, no social-network account, no scraping — nothing to get banned
- 💾 **Self-hosted + full data ownership** — Everything runs locally; browse your CRM in a web UI
- 🐳 **One-command setup** — Dockerized deployment, interactive onboarding
- ✨ **AI-powered messaging** — LLM-generated personalized outreach and agentic replies (bring your own model)

Perfect for founders, sales teams, and agencies who want powerful automation **without account bans or subscription lock-in**.

---

## 📋 What You Need

| # | What | Example |
|---|------|---------|
| 1 | **An LLM API key** | OpenAI, Anthropic, or any OpenAI-compatible endpoint |
| 2 | **An email-finder API key** ([BetterContact](https://bettercontact.rocks?fpr=openoutreach)) | Powers **both** discovery (Lead Finder) and enrichment (work-email resolution) |
| 3 | **A sending mailbox** | An app password for a mailbox you own (Gmail / Workspace / own-domain SMTP), or cold-email infra like [IceMail](https://icemail.ai?via=openoutreach) |
| 4 | **A product description + target market** | "We sell cloud cost optimization for DevOps teams at mid-market SaaS companies" |

That's it. No social-network account, no spreadsheets, no lead databases, no scraping setup. The BetterContact key and a connected mailbox are both required — the key drives discovery *and* enrichment, and the mailbox is where outreach is sent from.

---

## ⚡ Quick Start (Docker — Recommended)

Pre-built images are published to GitHub Container Registry.

```bash
docker run --pull always -it -v ~/.openoutreach/data:/app/data ghcr.io/eracle/openoutreach:latest
```

The interactive onboarding walks you through the inputs above on first run — product/objective → LLM key (live-verified) → mailbox (paste an app password → SMTP auth-check) → BetterContact key → your email → country → newsletter/legal. All data persists in `~/.openoutreach/data` on your host across restarts. The image is a slim Python runtime — **no browser, no VNC**.

For Docker Compose, build-from-source, and more options see the **[Docker Guide](./docs/docker.md)**.

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

### 2. Run the Daemon

```bash
make run
```
The interactive onboarding prompts for your LLM key, mailbox, BetterContact key, and campaign details on first run. Fully resumable — stop/restart anytime without losing progress.

### 3. Optional Agent/Script Controls

OpenOutreach also exposes a JSON management command for local operators and agents:

```bash
.venv/bin/python manage.py oo status --json
.venv/bin/python manage.py oo campaign list --json
.venv/bin/python manage.py oo lead list --campaign "Campaign" --json
.venv/bin/python manage.py oo task list --json
.venv/bin/python manage.py oo email send-next --campaign "Campaign" --dry-run --idempotency-key key-1 --json
.venv/bin/python manage.py oo email send-next --campaign "Campaign" --non-interactive --idempotency-key key-2 --json
.venv/bin/python manage.py oo audit list --json
```

Every command returns one stable JSON envelope so an external agent can monitor state, dry-run a send, execute a single idempotent opener send, and inspect the audit log without scraping daemon logs. This control plane manages an already-onboarded operator account; it does not create accounts or bypass onboarding.

### 4. View Your Data (CRM Admin)

OpenOutreach includes a full CRM web interface via Django Admin:
```bash
# Create an admin account (first time only)
python manage.py createsuperuser

# Start the web server
make admin
```
Then open:
- **Django Admin:** http://localhost:8000/admin/

---
## ✨ Features

| Feature                            | Description                                                                                                          |
|------------------------------------|----------------------------------------------------------------------------------------------------------------------|
| 🧠 **Autonomous Lead Discovery**   | No contact lists needed — an LLM turns your product + objective into an ICP filter and pages matching profiles from a licensed discovery source. |
| 🎯 **Bayesian Active Learning**    | Gaussian Process model on profile embeddings learns your ideal customer via explore/exploit, selecting the most informative candidates for LLM qualification. |
| 🔒 **Licensed Discovery**          | Firmographic profiles come from a paid, licensed provider (BetterContact Lead Finder) — no scraping, no browser, no account. |
| 📧 **Agentic Email Outreach**      | Resolves a work email per best-fit lead (one credit per hit), sends an AI-written opener from your own mailbox over SMTP, then reads replies (IMAP) and runs multi-turn follow-up. |
| 🔄 **Stateful Pipeline**          | Tracks deal states (`QUALIFIED` → `READY_TO_FIND_EMAIL` → `FINDING_EMAIL` → `READY_TO_EMAIL` → `SENDING_EMAIL` → `EMAILED` → `COMPLETED`/`FAILED`) in a local DB — fully resumable. |
| 🧾 **Agent Control Plane**        | JSON CLI commands let local agents monitor campaigns/tasks, execute idempotent one-send actions, and read an audit log. |
| ⏱️ **Send-Gated Spend**           | Paid email lookups ride on send capacity — a per-mailbox daily cap bounds how many leads enter the pipeline, so you never resolve more than you can send. |
| 💾 **Built-in CRM**               | Full data ownership via Django Admin — browse Leads, Deals, and conversations.                                     |
| 🐳 **One-Command Deployment**      | Dockerized setup with interactive onboarding; a slim runtime with no browser and no VNC.                            |
| ✍️ **AI-Powered Messaging**        | Agentic multi-turn follow-up conversations — the AI agent reads the thread, composes replies, and schedules future follow-ups. |

---

## 📖 How the Pipeline Works

The daemon runs a continuous **task queue** backed by a persistent `Task` model. Four task types self-schedule follow-on work:

| Task Type | What it does |
|-----------|-------------|
| **find_email** | Submits a work-email lookup for a ranked, qualified lead — a free hub-cache hit resolves immediately; otherwise it fires a paid provider job and parks the deal at `FINDING_EMAIL`. |
| **collect_email** | Polls the in-flight lookup (self-chaining backoff): hit → `READY_TO_EMAIL`, miss → `FAILED` (blank outcome, ML-skipped), couldn't-run/timeout → back to the queue. |
| **follow_up** | Runs the AI agent over an emailed deal — reads replies, decides send/wait/complete, and re-arms the next follow-up. |
| **email** | Claims one `READY_TO_EMAIL` deal as `SENDING_EMAIL`, sends an AI-written opener from your mailbox pool, then parks the deal at `EMAILED`. |

**Discover → qualify → gate → find email → email.** An LLM turns your campaign into an ICP filter (cached on the Campaign); discovery pages matching profiles into embedded `Lead`s. Qualification runs the GP + LLM loop over the stored firmographic text. The GP confidence gate promotes `QUALIFIED → READY_TO_FIND_EMAIL`, **rationing the paid lookup** so only the best-fit leads cost a credit. A hit sends an opener; a miss ends the deal as `FAILED` with a blank outcome (so the ML labeler skips it — an unfindable address is not a fit signal).

**The qualification loop in detail:**

Discovered profiles are embedded (384-dim FastEmbed vectors) from the licensed firmographic payload. The backfill chain decides which profile to evaluate next using a balance-driven strategy:

- **When negatives outnumber positives** → **exploit**: pick the profile with highest predicted qualification probability (fill the pipeline with likely positives)
- **Otherwise** → **explore**: pick the profile with highest BALD (Bayesian Active Learning by Disagreement) score (seek the most informative label)

All qualification decisions go through the LLM. The GP model selects which candidate to evaluate next and gates promotion from `QUALIFIED` to `READY_TO_FIND_EMAIL`. Every LLM decision feeds back into the model, making candidate selection progressively smarter.

**Cold start:** With fewer than 2 labelled profiles, the model can't fit — candidates are selected in order and qualified via LLM. As labels accumulate, the GP gets better at selecting high-value candidates. When the unlabelled pool empties, discovery pages a fresh batch.

Configure behavior via Django Admin (`SiteConfig` + `Campaign`).

---

## 📂 Project Structure

```
├── docs/                             # architecture, configuration, docker, templating, testing
├── openoutreach/                    # single source package; Django apps nested inside
│   ├── settings.py                  # Django settings (SQLite at data/db.sqlite3)
│   ├── core/                        # engine app: daemon, task queue + scheduler,
│   │                                #   Campaign/SiteConfig/Task, LLM factory, onboarding,
│   │                                #   ML + discovery/qualify pipeline, the two agents
│   ├── emails/                      # discovery/enrichment client, Mailbox + SMTP/IMAP,
│   │                                #   sender, the four task handlers
│   ├── crm/                         # Lead + Deal models
│   ├── chat/                        # ChatMessage (per-Deal conversation)
│   └── legacy/                      # model-less migration-history anchor (retired channel)
├── manage.py                         # Django management (no args defaults to rundaemon)
├── local.yml                        # Docker Compose
└── Makefile                         # Shortcuts (setup, run, admin, test)
```

---

## 📚 Documentation

- [Architecture](./docs/architecture.md)
- [Configuration](./docs/configuration.md)
- [Docker Installation](./docs/docker.md)
- [Follow-up Messaging](./docs/templating.md)
- [Template Variables](./docs/template-variables.md)
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

By using this software you accept the [Legal Notice](LEGAL_NOTICE.md). It covers the third-party services you connect (data provider, email-finder, mailbox), your responsibilities as data controller and sender under data-protection and anti-spam law, the optional freemium promotional campaign, automatic newsletter subscription for non-opt-in jurisdictions, the central contacts store, and liability disclaimers.

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
