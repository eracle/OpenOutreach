![OpenOutreach Logo](docs/logo.png)

> **Describe your product. Define your target market. The AI finds the leads for you.**

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

OpenOutreach is a **self-hosted, open-source LinkedIn automation tool** for B2B lead generation. Unlike other tools, **you don't need a list of profiles to contact** — you describe your product and your target market, and the system autonomously discovers, qualifies, and contacts the right people.

**How it works:**

1. **You provide** a product description and a campaign objective (e.g. "SaaS analytics platform" targeting "VP of Engineering at Series B startups")
2. **The AI generates** LinkedIn search queries to discover candidate profiles
3. **A Bayesian ML model** (Gaussian Process Regressor on profile embeddings) learns which profiles match your ideal customer — using an explore/exploit strategy to balance finding the best leads now vs. learning what makes a good lead
4. **An LLM classifies** each profile selected by the model; the GP learns from every decision to select better candidates over time
5. **Qualified leads** are automatically contacted, and an AI agent manages multi-turn follow-up conversations

The system gets smarter with every decision. It starts by exploring broadly, then progressively focuses on the highest-value profiles as it learns your ideal customer profile from its own classification history.

**Why choose OpenOutreach?**

- 🧠 **Autonomous lead discovery** — No contact lists needed; AI finds your ideal customers
- 🛡️ **Undetectable** — Playwright + stealth plugins mimic real user behavior
- 💾 **Self-hosted + full data ownership** — Everything runs locally, browse your CRM in a web UI
- 🐳 **One-command setup** — Dockerized deployment, interactive onboarding
- ✨ **AI-powered messaging** — LLM-generated personalized outreach (bring your own model)
- 🔌 **Multi-provider LLM** — Supports Google Gemini (default) and OpenAI, switchable from the UI
- 🖥️ **Professional CRM Dashboard** — Full web UI with lead/deal/campaign management, no terminal needed
- 👥 **Multi-account support** — Run multiple LinkedIn profiles simultaneously, each with independent automation

Perfect for founders, sales teams, and agencies who want powerful automation **without account bans or subscription lock-in**.

---

## 📋 What You Need

| # | What | Example |
|---|------|---------|
| 1 | **A LinkedIn account** | Your email + password |
| 2 | **An LLM API key** | Google Gemini (default), OpenAI, or any OpenAI-compatible endpoint |
| 3 | **A product description + target market** | "We sell cloud cost optimization for DevOps teams at mid-market SaaS companies" |

That's it. No spreadsheets, no lead databases, no scraping setup.

---

## ⚡ Quick Start (Docker — Recommended)

Pre-built images are published to GitHub Container Registry on every push to `master`.

```bash
docker run --pull always -it -p 5900:5900 -p 6080:6080 -v openoutreach_db:/app/data ghcr.io/eracle/openoutreach:latest

# Open http://localhost:6080/vnc.html in your browser to watch the automation live
```

The interactive onboarding walks you through the three inputs above on first run. All data persists in the `openoutreach_db` Docker volume across restarts.

Once the container is running, open **http://localhost:6080/vnc.html** in your browser to watch the browser live (noVNC). Alternatively, connect a native VNC client to `localhost:5900`.

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

### 3. CRM Web Interface

OpenOutreach includes a full professional CRM web interface:
```bash
make admin
```
Then open **http://localhost:8000/** in your browser.

**First-time setup:** On first visit, you'll be prompted to create an admin account. After that, you'll land on the CRM dashboard.

**What you can do from the CRM:**
- 📊 **Dashboard** — overview of leads, deals, pipeline, and activity
- 👥 **Leads & Deals** — search, filter, and manage your pipeline
- 🎯 **Campaigns** — create and configure outreach campaigns
- 🔑 **Accounts** — add LinkedIn profiles, start/stop automation per account (no terminal needed)
- ⚙️ **Settings** — configure LLM provider (Gemini/OpenAI), API keys, and model selection
- 📋 **Tasks & Activity** — monitor task queue and action history

> **Tip:** You can also manage everything via Django Admin at http://localhost:8000/admin/

---
## ✨ Features

| Feature                            | Description                                                                                                          |
|------------------------------------|----------------------------------------------------------------------------------------------------------------------|
| 🧠 **Autonomous Lead Discovery**   | No contact lists needed — LLM generates search queries from your product description and campaign objective.         |
| 🎯 **Bayesian Active Learning**    | Gaussian Process model on profile embeddings learns your ideal customer via explore/exploit, selecting the most informative candidates for LLM qualification. |
| 🤖 **Stealth Browser Automation**  | Playwright + stealth plugins mimic real user behavior for undetectable interactions.                                 |
| 🛡️ **Voyager API Scraping**       | Uses LinkedIn's internal API for accurate, structured profile data (no fragile HTML parsing).                        |
| 🔄 **Stateful Pipeline**          | Tracks profile states (`QUALIFIED` → `READY_TO_CONNECT` → `PENDING` → `CONNECTED` → `COMPLETED`) in a local DB — fully resumable. |
| ⏱️ **Smart Rate Limiting**        | Configurable daily/weekly limits per action type, respects LinkedIn's own limits automatically.                      |
| �️ **Professional CRM UI**       | Full web dashboard with leads, deals, campaigns, tasks, activity log — built with Tailwind CSS, HTMX, and Chart.js. |
| 🔌 **Multi-Provider LLM**         | Supports Google Gemini (default) and OpenAI. Switch provider and model from the Settings page — no code changes.     |
| 👥 **Multi-Account Support**       | Run multiple LinkedIn profiles simultaneously, each with its own browser and daemon thread.                          |
| 🎛️ **Web-Based Controls**         | Start/stop automation per account from the CRM — no terminal access needed.                                          |
| 🚀 **First-Time Setup Wizard**    | Guided admin account creation on first launch. No CLI setup required.                                                |
| 🐳 **One-Command Deployment**      | Dockerized setup with interactive onboarding and VNC browser view (`localhost:5900`).                                |
| ✍️ **AI-Powered Messaging**        | Agentic multi-turn follow-up conversations — the AI agent reads history, sends messages, and schedules future follow-ups. |

---

## 📖 How the ML Pipeline Works

The daemon runs a continuous **task queue** backed by a persistent `Task` model. Three task types self-schedule follow-on work:

| Task Type | What it does |
|-----------|-------------|
| **Connect** | Ranks qualified profiles by GP model probability, sends connection requests (daily + weekly limits). Triggers qualification and search via composable generators when the pool is empty. |
| **Check Pending** | Checks if a pending request was accepted (exponential backoff per profile) |
| **Follow Up** | Runs an AI agent that manages multi-turn conversations with connected profiles |

**The qualification loop in detail:**

Profiles discovered during navigation are automatically scraped and embedded (384-dim FastEmbed vectors). The connect task's backfill chain decides which profile to evaluate next using a balance-driven strategy:

- **When negatives outnumber positives** → **exploit**: pick the profile with highest predicted qualification probability (seek likely positives to fill the pipeline)
- **Otherwise** → **explore**: pick the profile with highest BALD (Bayesian Active Learning by Disagreement) score (seek the most informative label to improve the model)

All qualification decisions go through the LLM. The GP model selects which candidate to evaluate next and gates promotion from QUALIFIED to READY_TO_CONNECT (confidence threshold). Every LLM decision feeds back into the model, making candidate selection progressively smarter.

**Cold start:** With fewer than 2 labelled profiles, the model can't fit — candidates are selected in order and qualified via LLM. As labels accumulate, the GP becomes better at selecting high-value candidates.

Configure rate limits and behavior via Django Admin (LinkedInProfile + Campaign models).

---

## 📂 Project Structure

```
├── docs/
│   ├── architecture.md              # System architecture
│   ├── configuration.md             # Configuration reference
│   ├── docker.md                    # Docker setup guide
│   ├── templating.md                # Follow-up messaging guide
│   └── testing.md                   # Testing strategy
├── linkedin/
│   ├── actions/                     # Browser actions (connect, message, status, search)
│   ├── agents/                      # Follow-up agent (multi-turn conversations)
│   ├── api/                         # Voyager API client + parser + messaging package
│   ├── browser/                     # Session management, login, navigation
│   ├── conf.py                      # Configuration + LLM factory (Gemini/OpenAI)
│   ├── daemon.py                    # Task queue worker loop (supports stop_event)
│   ├── daemon_manager.py            # Thread-based daemon lifecycle per profile
│   ├── db/                          # CRM-backed CRUD (leads, deals, enrichment, chat)
│   ├── django_settings.py           # Django settings (SQLite at data/db.sqlite3)
│   ├── management/                  # Management commands (rundaemon, setup_crm, etc.)
│   ├── ml/                          # Bayesian qualifier (GPR), embeddings, profile text
│   ├── models.py                    # Django models (SiteConfig, Campaign, LinkedInProfile, Task)
│   ├── onboarding.py                # Interactive onboarding wizard
│   ├── pipeline/                    # Candidate sourcing, qualification, pool management
│   ├── setup/                       # GDPR, self-profile, freemium campaign setup
│   ├── tasks/                       # Task handlers (connect, check_pending, follow_up)
│   └── urls.py                      # Root URL config (CRM, admin, setup, auth)
├── crm/
│   ├── models/                      # Lead + Deal models
│   ├── views.py                     # CRM web UI views (15+ pages)
│   ├── urls.py                      # CRM URL routing (20 patterns)
│   ├── middleware.py                # First-time setup redirect middleware
│   ├── setup_views.py               # One-time admin account creation
│   └── setup_urls.py                # Setup URL routing
├── chat/
│   └── models.py                    # ChatMessage model
├── templates/crm/                   # Tailwind CSS + HTMX templates (17 templates)
│   ├── base.html                    # Sidebar layout
│   ├── setup.html                   # First-time admin setup
│   ├── login.html                   # Login page
│   ├── dashboard.html               # Dashboard with charts
│   ├── accounts.html                # Accounts with daemon controls
│   ├── campaigns.html, campaign_create.html  # Campaign management
│   ├── leads.html, deals.html       # Lead/Deal lists
│   ├── settings.html                # LLM + profile settings
│   └── partials/                    # HTMX partial templates
├── manage.py                         # Django management (no args defaults to rundaemon)
├── local.yml                        # Docker Compose
├── Makefile                         # Shortcuts (setup, run, admin, test)
└── CHANGELOG.md                     # Version history
```

---

## 📚 Documentation

- [Architecture](./docs/architecture.md)
- [Configuration](./docs/configuration.md)
- [Profile Lifecycle](./docs/profile_lifecycle.md)
- [Docker Installation](./docs/docker.md)
- [Follow-up Messaging](./docs/templating.md)
- [Template Variables](./docs/template-variables.md)
- [Testing](./docs/testing.md)

---

## 💬 Community

Join for support and discussions:
[Telegram Group](https://t.me/+Y5bh9Vg8UVg5ODU0)

---

### 🗓️ Book a Free 15-Minute Call

Got a specific use case, feature request, or questions about setup?

Book a **free 15-minute call** — I'd love to hear your needs and improve the tool based on real feedback.

<div align="center">

[![Book a 15-min call](https://img.shields.io/badge/Book%20a%2015--min%20call-28A745?style=for-the-badge&logo=calendar)](https://calendly.com/eracle/new-meeting)

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

**Not affiliated with LinkedIn.**

By using this software you accept the [Legal Notice](LEGAL_NOTICE.md). It covers LinkedIn ToS risks, built-in self-promotional actions, automatic newsletter subscription for non-GDPR accounts, and liability disclaimers.

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
