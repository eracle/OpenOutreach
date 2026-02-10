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

## ⚡ Quick Start (Local Installation)

Get up and running in minutes by running the application directly on your machine.

### Prerequisites

- [Git](https://git-scm.com/)
- [Python](https://www.python.org/downloads/) (3.11+ recommended)
- `venv` for creating virtual environments (usually included with Python)

### 1. Clone the Repository
```bash
git clone https://github.com/eracle/OpenOutreach.git
cd OpenOutreach
```

### 2. Set Up a Virtual Environment
It's highly recommended to use a virtual environment to manage dependencies.
```bash
# Create the virtual environment
python -m venv venv

# Activate it
source venv/bin/activate          # Windows: venv\Scripts\activate
```

### 3. Install Dependencies & Set Up the CRM
We use `uv` for fast dependency management and DjangoCRM for the local database.
```bash
# Install deps, run migrations, and bootstrap CRM data
make setup

# Install required browser assets
playwright install --with-deps chromium
```

### 4. Configure the Application
You need to provide your LinkedIn credentials and target profiles.

1. **Configure LinkedIn accounts + optional OpenAI key**
   ```bash
   cp assets/accounts.secrets.template.yaml assets/accounts.secrets.yaml
   ```
   Edit `assets/accounts.secrets.yaml` with your credentials (and add your OpenAI key under `env:` if you want AI follow-ups).

2. **Add target profiles**
   Paste LinkedIn profile URLs into `assets/inputs/urls.csv`.

### 5. Run the Application

```bash
make run                    # run with first active account
make run HANDLE=myhandle    # run with a specific account
```
The tool is fully resumable — stop/restart anytime without losing progress.

### 6. View Your Data (CRM Admin)

OpenOutreach includes a full CRM web interface powered by DjangoCRM:
```bash
# Create an admin account (first time only)
python manage_crm.py createsuperuser

# Start the web server
make admin
```
Then open:
- **Django Admin:** http://localhost:8000/admin/
- **CRM UI:** http://localhost:8000/crm/
---

## 🐳 Docker Installation

We also support running the application via Docker. This is a great option for ensuring a consistent environment and simplifying dependency management.

For full instructions, please see the **[Docker Installation Guide](./docs/docker.md)**.

---
## ✨ Features

| Feature                            | Description                                                                                                          |
|------------------------------------|----------------------------------------------------------------------------------------------------------------------|
| 🤖 **Advanced Browser Automation** | Powered by Playwright with stealth plugins for human-like, undetectable interactions.                                |
| 🛡️ **Reliable Data Scraping**     | Uses LinkedIn's internal Voyager API for accurate, structured profile data (no fragile HTML parsing).                |
| 🐍 **Python-Native Campaigns**     | Write flexible, powerful automation sequences directly in Python.                                                    |
| 🔄 **Stateful Workflow Engine**    | Tracks profile states (`DISCOVERED` → `ENRICHED` → `CONNECTED` → `COMPLETED`) in a local DB -- resumable at any time. |
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

The default campaign (`linkedin/campaigns/connect_follow_up.py`) handles:

- Profile enrichment
- Connection requests
- Personalized follow-ups

**Profile states:** `DISCOVERED` → `ENRICHED` → `CONNECTED` → `COMPLETED` (or `FAILED`)

Edit the campaign file directly for custom logic, templates, or AI integration.

---

## 📂 Project Structure

```
├── assets/
│   ├── accounts.secrets.yaml      # LinkedIn credentials
│   └── inputs/
│       └── urls.csv               # Target profiles
├── docs/
│   ├── docker.md                  # NEW: Docker setup guide
│   └── ...
├── linkedin/
│   ├── actions/                   # Browser actions
│   ├── api/                       # Voyager API client
│   ├── campaigns/                 # Workflows
│   ├── db/crm_profiles.py         # CRM-backed profile CRUD (Lead, Contact, Company, Deal)
│   ├── django_settings.py         # Django/CRM settings (SQLite at assets/data/crm.db)
│   ├── management/setup_crm.py    # Idempotent CRM bootstrap (Dept, Stages, Users)
│   ├── navigation/                # Login helpers
│   └── sessions/                  # Session management
├── main.py                        # Entry point (bootstraps Django)
├── manage_crm.py                  # Django manage.py (migrate, runserver, createsuperuser)
├── local.yml                      # Docker Compose
└── Makefile                       # Shortcuts (setup, run, admin, test, etc.)
```

---

## 📚 Documentation

- [Docker Installation](./docs/docker.md)
- [Configuration](./docs/configuration.md)
- [Templating](./docs.md)
- [Testing Strategy](./docs/testing.md)

---

## 💬 Community

Join for support and discussions:  
[Telegram Group](https://t.me/+Y5bh9Vg8UVg5ODU0)

---

## ⚖️ License

[GNU GPLv3](https://www.gnu.org/licenses/gpl-3.0) — see [LICENCE.md](LICENCE.md)

---

## 📜 Legal Disclaimer

**Not affiliated with LinkedIn.**

Automation may violate LinkedIn's terms (Section 8.2). Risk of account suspension exists.

**Use at your own risk — no liability assumed.**

---

<div align="center">

**Made with ❤️**

</div>
