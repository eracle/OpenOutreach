![Logo](docs/logo.png)

> The open-source growth engine that puts your LinkedIn B2B lead generation on autopilot.


[![GitHub stars](https://img.shields.io/github/stars/eracle/OpenOutreach.svg)](https://github.com/eracle/OpenOutreach/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/eracle/OpenOutreach.svg)](https://github.com/eracle/OpenOutreach/network)
[![MIT License](https://img.shields.io/badge/License-MIT-green.svg)](https://choosealicense.com/licenses/mit/)
[![Open Issues](https://img.shields.io/github/issues/eracle/OpenOutreach)](https://github.com/eracle/OpenOutreach/issues)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square)](http://makeapullrequest.com)


## Quick Start

```bash
git clone https://github.com/eracle/OpenOutreach.git
cd OpenOutreach
make up
```

## Table of Contents
- [Features](#features)
- [Installation](#installation)
- [Usage](#usage)
- [Project Structure](#project-structure)
- [Configuration](#configuration)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [Community](#community)
- [License](#license)

## Features
✓ **Advanced Browser Automation:** Utilizes Playwright for robust, human-like interaction with a stealth plugin to minimize detection.  
✓ **Reliable Data Scraping:** Bypasses traditional HTML scraping by using LinkedIn's internal Voyager API for structured and reliable profile data.  
✓ **Python-Native Campaigns:** Define sophisticated automation sequences directly in Python for maximum flexibility and control.  
✓ **Stateful Workflow Engine:** Tracks the state of each profile (e.g., `DISCOVERED`, `ENRICHED`, `CONNECTED`, `COMPLETED`) in a local database, ensuring campaigns can be stopped and resumed without losing progress.  
✓ **Persistent Local Database:** Full ownership and access to your data with a dedicated SQLite database for each LinkedIn account.  
✓ **Containerized Environment:** One-command builds and deployment with Docker and Make.  
✓ **Visual Debugging:** Watch the automation in real-time with a built-in VNC server, accessible at `localhost:5900`.
✓ **AI-Ready Templating:** Easily integrate with generative AI (e.g., GPT) for hyper-personalized messages using a powerful prompt-based templating system.

## Installation

### Prerequisites
- make
- Docker
- Docker Compose
- Git

### Steps
1. **Clone the repository:**
   ```bash
   git clone https://github.com/eracle/OpenOutreach.git
   cd OpenOutreach
   ```

2. **Configure Accounts:**
   Create your account secrets file from the template.
   ```bash
   cp assets/accounts.secrets.template.yaml assets/accounts.secrets.yaml
   ```
   Edit `assets/accounts.secrets.yaml` and add your LinkedIn account credentials. You can add multiple accounts.

3. **Define Targets:**
   Add the LinkedIn profile URLs you want to target to the `assets/inputs/urls.csv` file.

4. **Build and Run:**
   Use the `Makefile` to build and run the Docker containers.
   ```bash
   make up
   ```
   The application will start, pick the first active account from your secrets file, and begin processing the profiles in `urls.csv`.

## Usage

The primary workflow is the **Connect and Follow-Up Campaign**, defined in `linkedin/campaigns/connect_follow_up.py`. This campaign automates the process of building your network and initiating conversations.

The workflow operates as a state machine for each profile loaded from `assets/inputs/urls.csv`:

1.  **`DISCOVERED`**: The initial state for a new profile URL.
2.  **`ENRICHED`**: The bot scrapes the profile using LinkedIn's internal API to gather detailed, structured data. This data is saved to the local SQLite database (`assets/data/<your_handle>.db`) for future use.
3.  **`CONNECTED`**: The bot sends a connection request. The state is updated once the connection is accepted.
4.  **`COMPLETED`**: After a successful connection, the bot sends a personalized follow-up message using the templates defined in the campaign file.
5.  **`FAILED`**: If any step fails, the profile is marked as `FAILED` for later review.

To run the campaign, simply execute `make up`. The system is designed to be resumable, so if you stop and restart it, it will pick up where it left off for each profile.

You can modify the campaign logic and message templates directly in the `linkedin/campaigns/connect_follow_up.py` file.

## Project Structure

```
├── assets/
│   ├── accounts.secrets.yaml   # Your LinkedIn credentials (add this yourself)
│   └── inputs/
│       └── urls.csv            # Target profile URLs
├── linkedin/
│   ├── actions/                # Reusable low-level browser tasks (e.g., send message)
│   ├── api/                    # Client for LinkedIn's internal Voyager API
│   ├── campaigns/              # High-level automation workflows
│   ├── db/                     # Database models and functions (SQLite)
│   ├── navigation/             # Browser control, login, and utilities
│   └── sessions/               # Manages account sessions and state
├── main.py                     # Main entry point for the application
├── local.yml                   # Docker Compose configuration
└── Makefile                    # Helper commands for build, run, test, etc.
```

## Configuration

-   **`assets/accounts.secrets.yaml`**: Manage your LinkedIn account(s). Set `active: true` for the accounts you want to run.
-   **`assets/inputs/urls.csv`**: The list of LinkedIn profile URLs to target.
-   **`linkedin/campaigns/connect_follow_up.py`**: Configure campaign-specific settings, such as template paths and types (`jinja` or `ai_prompt`).



## Community
- [GitHub Discussions](https://github.com/eracle/OpenOutreach/discussions)
- [Telegram Group](https://t.me/+Y5bh9Vg8UVg5ODU0).


## License
This project is licensed under the MIT License - see the [LICENCE.md](LICENCE.md) file for details.

## Legal Disclaimer

This code is not affiliated with, authorized, maintained, sponsored, or endorsed by LinkedIn or any of its affiliates or
subsidiaries. This is an independent and unofficial project. Use at your own risk.

This project violates LinkedIn's User Agreement Section 8.2. As a result, LinkedIn may temporarily or permanently ban
your account. We are not responsible for any actions taken by LinkedIn in response to the use of this tool.

---

Made with ❤️