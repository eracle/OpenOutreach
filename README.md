# Linkedin Automation

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=eracle/linkedin&type=Date)](https://star-history.com/#eracle/linkedin&Date)

### Introduction

This open-source tool is designed to streamline and automate interactions on LinkedIn using browser automation
techniques powered by Playwright. It enables users to create and manage campaigns for
tasks like searching profiles, sending personalized connection requests, and following up with AI-generated messages
tailored to individual profiles. With features such as multi-account support, scheduling for drip campaigns, a local
database (using DuckDB or SQLite) for storing data and tracking metrics, and strong analytics for monitoring engagement,
this project aims to enhance productivity for networking and outreach efforts. Built with stealth features to mimic
human behavior, it emphasizes ethical use while providing a flexible YAML-based configuration. Please note the legal
disclaimer: this is an unofficial tool that may violate LinkedIn's terms, so proceed with caution and at your own risk.
Join our Telegram group for discussions, and check the star history to see community interest!

### Join our [Telegram Group](https://t.me/+Y5bh9Vg8UVg5ODU0).

---

- **Automation Engine**
    - [x] Set up Playwright for browser automation
    - [x] Implement login functionality on LinkedIn
    - [x] Develop interaction features (e.g., clicking, typing) on LinkedIn elements
    - [x] Integrate stealth features to mimic human behavior and avoid detection

- **Campaigns**
    - [x] Definition of action sequences
    - [ ] Support creation of action sequences
    - [ ] Enable execution of action sequences
    - [ ] Implement profile search functionality
    - [ ] Add sending of personalized connection requests
    - [ ] Develop follow-up message capabilities
    - [ ] Configure campaigns using YAML files

- **Future Planning/Scheduling**
    - [ ] Implement time-based action queuing
    - [ ] Set up drip campaigns
    - [ ] Add support for recurring tasks
    - [ ] Use async libraries for scheduling
    - [ ] Add persistence mechanisms for resuming interrupted sessions

- **Local Database**
    - [ ] Integrate DuckDB as the primary database
    - [ ] Set up fallback to SQLite if needed
    - [ ] Store scraped data in the database
    - [ ] Log campaign activities
    - [ ] Store schedules and tasks
    - [ ] Track metrics in the database
    - [ ] Leverage DuckDB's analytical features for querying data

- **Personalized Messages with AI**
    - [ ] Integrate open AI models
    - [ ] Scrape profile data for personalization
    - [ ] Generate customized messages based on profile data

- **Multi accounts support**
    - [ ] Allow multiple accounts to be stored in the db
    - [ ] Execute multiple accounts campaigns

- **Strong Analytics**
    - [ ] Track action success rates
    - [ ] Monitor engagement metrics (e.g., connection accepts, replies)
    - [ ] Log errors for analysis
    - [ ] Develop dashboards for viewing metrics

## Installation and Setup

You will need the following:

- Docker and docker compose
- A VNC viewer (e.g., Vinagre for Ubuntu)

### Steps

1. **Prepare your environment**: Install Docker from the [official website](https://www.docker.com/). If you don't have
   a VNC viewer, install one. For Ubuntu, you can use Vinagre:

```bash
sudo apt-get update
sudo apt-get install vinagre
```

## Legal Disclaimer

This code is not affiliated with, authorized, maintained, sponsored, or endorsed by LinkedIn or any of its affiliates or
subsidiaries. This is an independent and unofficial project. Use at your own risk.

This project violates LinkedIn's User Agreement Section 8.2. As a result, LinkedIn may temporarily or permanently ban
your account. We are not responsible for any actions taken by LinkedIn in response to the use of this tool.

---
