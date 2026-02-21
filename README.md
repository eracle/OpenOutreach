![OpenOutreach Logo](docs/logo.png)

> **The open-source growth engine that puts your LinkedIn B2B lead generation on autopilot.**

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

OpenOutreach is a **self-hosted, open-source LinkedIn automation tool** designed for B2B lead generation, without the risks and costs of cloud SaaS services.

It automates the entire outreach process in a **stealthy, human-like way**:

- Discovers and enriches target profiles
- Qualifies and ranks profiles using online Bayesian active learning (BALD acquisition + entropy-gated auto-decisions)
- Sends personalized connection requests
- Follows up with custom messages after acceptance
- Tracks everything in a built-in CRM with web UI (full data ownership, resumable workflows)

**Why choose OpenOutreach?**

- 🛡️ **Undetectable** — Playwright + stealth plugins mimic real user behavior
- 🐍 **Fully customizable** — Python-based campaigns for unlimited flexibility
- 💾 **Local execution + CRM** — You own your data, browse it in a web UI
- 🐳 **Easy deployment** — Dockerized, one-command setup
- ✨ **AI-ready** — Built-in templating for hyper-personalized messages (easy integration with latest models like GPT-5.3-Codex)

Perfect for founders, sales teams, and agencies who want powerful automation **without account bans or subscription lock-in**.

---

## ⚡ Quick Start (Docker — Recommended)

The fastest way to get started. Pre-built images are published to GitHub Container Registry on every push to `master`.

```bash
docker run --pull always -it -p 5900:5900 -v openoutreach_data:/app/assets ghcr.io/eracle/openoutreach:latest
```

The interactive onboarding will prompt you for LinkedIn credentials, LLM API key, and campaign details on first run. Your data persists in the `openoutreach_data` Docker volume across restarts.

Connect a VNC client to `localhost:5900` to watch the browser live.

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

# Install deps, Playwright browsers, run migrations, and bootstrap CRM
make setup
```

### 2. Run the Daemon

```bash
make run
```
The interactive onboarding will prompt for LinkedIn credentials, LLM API key, and campaign details on first run. Fully resumable — stop/restart anytime without losing progress.

### 3. View Your Data (CRM Admin)

OpenOutreach includes a full CRM web interface powered by DjangoCRM:
```bash
# Create an admin account (first time only)
python manage.py createsuperuser

# Start the web server
make admin
```
Then open:
- **Django Admin:** http://localhost:8000/admin/
- **CRM UI:** http://localhost:8000/crm/

---
## ✨ Features

| Feature                            | Description                                                                                                          |
|------------------------------------|----------------------------------------------------------------------------------------------------------------------|
| 🤖 **Advanced Browser Automation** | Powered by Playwright with stealth plugins for human-like, undetectable interactions.                                |
| 🛡️ **Reliable Data Scraping**     | Uses LinkedIn's internal Voyager API for accurate, structured profile data (no fragile HTML parsing).                |
| 🐍 **Python-Native Campaigns**     | Write flexible, powerful automation sequences directly in Python.                                                    |
| 🧠 **ML-Driven Qualification**    | Gaussian Process Classifier with BALD active learning qualifies and ranks profiles -- lazily re-fitted on all accumulated labels when predictions are needed. |
| 🔄 **Stateful Workflow Engine**    | Tracks profile states (`DISCOVERED` → `ENRICHED` → `QUALIFIED` → `PENDING` → `CONNECTED` → `COMPLETED`) in a local DB -- resumable at any time. |
| ⏱️ **Smart Rate Limiting**        | Configurable daily/weekly limits per action type, respects LinkedIn's own limits automatically. |
| 💾 **Built-in CRM**               | Full data ownership via DjangoCRM with Django Admin UI -- browse Leads, Contacts, Companies, and Deals in your browser. |
| 🐳 **Containerized Setup**         | One-command Docker + Make deployment.                                                                                |
| 🖥️ **Visual Debugging**           | Real-time browser view via built-in VNC server (`localhost:5900`).                                                   |
| ✍️ **AI-Ready Templating**         | Jinja or AI-prompt templates for hyper-personalized messages (plug in latest models like GPT-5.3-Codex easily).     |

---

### ❤️ Support OpenOutreach – Keep the Leads Flowing!

This project is built in spare time to provide powerful, **free** open-source growth tools.

Maintaining stealth, fixing bugs, adding features (multi-account scaling, better templates, AI enhancements), and staying ahead of LinkedIn changes takes serious effort.

**Your sponsorship funds faster updates and keeps it free for everyone.**

<div align="center">

[![Sponsor with GitHub](https://img.shields.io/badge/Sponsor-%E2%9D%A4-ff69b4?style=for-the-badge&logo=github)](https://github.com/sponsors/eracle)

<br/>

**Popular Tiers & Perks:**

| Tier        | Monthly | Benefits                                                              |
|-------------|---------|-----------------------------------------------------------------------|
| ☕ Supporter | $5      | Huge thanks + name in README supporters list                          |
| 🚀 Booster  | $25     | All above + priority feature requests + early access to new campaigns |
| 🦸 Hero     | $100    | All above + personal 1-on-1 support + influence roadmap               |
| 💎 Legend   | $500+   | All above + custom feature development + shoutout in releases         |

**Thank you to all sponsors — you're powering open-source B2B growth!** 🚀

</div>

---

### 🗓️ Book a Free 15-Minute Call

Got a specific use case, feature request, or questions about setup?

Book a **free 15-minute call** — I’d love to hear your needs and improve the tool based on real feedback.

<div align="center">

[![Book a 15-min call](https://img.shields.io/badge/Book%20a%2015--min%20call-28A745?style=for-the-badge&logo=calendar)](https://calendly.com/eracle/new-meeting)

</div>

---

## 📖 Usage & Customization

The daemon (`linkedin/daemon.py`) priority-schedules five action lanes:

| Lane | What it does | Rate limited? |
|------|-------------|---------------|
| **Connect** | Ranks QUALIFIED profiles by GPC posterior probability, sends connection requests | Daily + weekly limits |
| **Check Pending** | Checks if PENDING requests were accepted | Exponential backoff |
| **Follow Up** | Sends personalized messages to CONNECTED profiles | Daily limit |
| **Enrich** | Scrapes DISCOVERED profiles via LinkedIn's Voyager API, computes embeddings | Gap-filling |
| **Qualify** | Qualifies ENRICHED profiles via Bayesian active learning (BALD selects, entropy gates LLM calls) | Gap-filling |
| **Search** | Discovers new profiles via LLM-generated LinkedIn People search keywords | Lowest-priority gap-filler |

**Profile states:** `DISCOVERED` → `ENRICHED` → `QUALIFIED` → `PENDING` → `CONNECTED` → `COMPLETED` (or `FAILED` / `IGNORED` / `DISQUALIFIED`)

Pre-existing connections (already connected before automation) are automatically set to `IGNORED` during enrichment. If `connection_degree` was unknown at scrape time, they're caught during the connect step. Profiles rejected by the qualification pipeline are set to `DISQUALIFIED`.

Configure rate limits, timing, and behavior in the `campaign:` section of `accounts.secrets.yaml`.

---

## 📂 Project Structure

```
├── analytics/                       # dbt project (DuckDB analytics, ML training sets)
│   ├── models/staging/              # Staging views (stg_leads, stg_deals, stg_stages)
│   └── models/marts/                # ML training set (ml_connection_accepted)
├── assets/
│   ├── accounts.secrets.yaml        # Credentials + campaign + LLM config (gitignored)
│   ├── inputs/                      # Optional input files
│   ├── campaign/                    # Onboarding files (product_docs.txt, campaign_objective.txt)
│   └── data/                        # crm.db (SQLite), analytics.duckdb (embeddings + analytics)
├── docs/
│   ├── architecture.md              # System architecture
│   ├── configuration.md             # Configuration reference
│   ├── docker.md                    # Docker setup guide
│   ├── templating.md                # Message template guide
│   └── testing.md                   # Testing strategy
├── linkedin/
│   ├── actions/                     # Browser actions (connect, message, scrape)
│   ├── api/                         # Voyager API client + parser
│   ├── conf.py                      # Configuration loading (secrets YAML + env vars)
│   ├── daemon.py                    # Main daemon loop (priority-scheduled lanes)
│   ├── db/crm_profiles.py           # CRM-backed profile CRUD (Lead, Contact, Company, Deal)
│   ├── django_settings.py           # Django/CRM settings (SQLite at assets/data/crm.db)
│   ├── lanes/                       # Action lanes (enrich, qualify, connect, check_pending, follow_up, search)
│   ├── management/setup_crm.py      # Idempotent CRM bootstrap (Dept, Stages, Users)
│   ├── ml/                          # Bayesian qualifier, DuckDB embeddings, profile text, search keywords
│   ├── navigation/                  # Login, throttling, browser utilities, enums
│   ├── onboarding.py                # Interactive onboarding (product docs + campaign objective)
│   ├── gdpr.py                      # GDPR location detection for newsletter
│   ├── rate_limiter.py              # Daily/weekly rate limiting
│   ├── sessions/                    # Session management (AccountSession)
│   └── templates/                   # Message rendering (Jinja2 / AI-prompt)
├── manage.py                         # Entry point (no args = daemon, or Django commands)
├── local.yml                        # Docker Compose
└── Makefile                         # Shortcuts (setup, run, admin, analytics, test)
```

---

## 📚 Documentation

- [Architecture](./docs/architecture.md)
- [Configuration](./docs/configuration.md)
- [Docker Installation](./docs/docker.md)
- [Templating](./docs/templating.md)
- [Template Variables](./docs/template-variables.md)
- [Testing](./docs/testing.md)
- [MCP Server](./docs/mcp.md)

---

## 💬 Community

Join for support and discussions:  
[Telegram Group](https://t.me/+Y5bh9Vg8UVg5ODU0)

---

## ⚖️ License

[GNU GPLv3](https://www.gnu.org/licenses/gpl-3.0) — see [LICENCE.md](LICENCE.md)

---

## 📜 Legal Notice

**Not affiliated with LinkedIn.**

By using this software you accept the [Legal Notice](LEGAL_NOTICE.md). It covers LinkedIn ToS risks, built-in self-promotional actions, automatic newsletter subscription for non-GDPR accounts, and liability disclaimers.

**Use at your own risk — no liability assumed.**

---

<div align="center">

**Made with ❤️**

</div>
