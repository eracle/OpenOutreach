# Linkedin Automation

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=eracle/linkedin&type=Date)](https://star-history.com/#eracle/linkedin&Date)

### Introduction
#### TLDR:
Linkedin Automation tool, sequence of actions:
- visit profile
- send connection request
- send personalized message with AI
- repeat


This open-source tool is designed to streamline and automate interactions on LinkedIn using browser automation
techniques powered by Playwright. It enables users to create and manage campaigns for
tasks like searching profiles, sending personalized connection requests, and following up with AI-generated messages
tailored to individual profiles. With features such as multi-account support, a local
database (using SQLite) for storing data and tracking key performance metrics,
this project aims to enhance productivity for networking and outreach efforts. Built with stealth features to mimic
human behavior, it emphasizes ethical use while providing flexible Python-based campaign definitions. Please note the
legal disclaimer: this is an unofficial tool that may violate LinkedIn's terms, so proceed with caution and at your own
risk.

Join our Telegram group for discussions!

### Join our [Telegram Group](https://t.me/+Y5bh9Vg8UVg5ODU0).

---

- **Automation Engine**
    - [x] Set up Playwright for browser automation
    - [x] Implement login functionality on LinkedIn
    - [x] Develop interaction features (e.g., clicking, typing) on LinkedIn elements
    - [x] Integrate stealth features to mimic human behavior and avoid detection

- **Campaigns**
    - [x] Definition of action sequences
    - [x] Support creation of action sequences
    - [x] Enable execution of action sequences
    - [x] Implement profile search functionality
    - [x] Add sending of personalized connection requests
    - [x] Develop follow-up message capabilities
    - [x] Configure campaigns using Python modules

- **Local Database**
    - [x] Integrate SQLite as the primary database
    - [x] Store scraped data in the database
    - [x] Log campaign activities
    - [x] Track metrics in the database

- **Personalized Messages with AI**
    - [x] Integrate open AI models
    - [x] Scrape profile data for personalization
    - [x] Generate customized messages based on profile data

- **Multi accounts support**
    - [x] Allow multiple accounts to be stored in the db
    - [ ] Execute multiple accounts campaigns

- **Strong Analytics**
    - [x] Track action success rates
    - [ ] Monitor engagement metrics (e.g., connection accepts, replies)
    - [ ] Log errors for analysis
    - [ ] Develop dashboards for viewing metrics

- **Architecture**
    - For a detailed explanation of the system's design, data models, and workflows, please see
      the [System Architecture Documentation](./docs/architecture.md).

- **Documentation**
    - [Configuration](./docs/configuration.md)
    - [Templating](./docs/templating.md)
    - [Testing Strategy](./docs/testing_strategy.md)

## Quick Start

0. ** Install Vinagre (Ubuntu): **

```bash
sudo apt-get update
sudo apt-get install vinagre
```

1. **Build the Docker containers**:
    ```bash
    make build
    ```

2. **Start the application**:
    ```bash
    make up-view
    ```
3. **Stop the application**:
    ```bash
    make stop
    ```
4. **View logs**:
    ```bash
    make attach
    ```
5. **Run tests**:
    ```bash
    make test
    ```


## Legal Disclaimer

This code is not affiliated with, authorized, maintained, sponsored, or endorsed by LinkedIn or any of its affiliates or
subsidiaries. This is an independent and unofficial project. Use at your own risk.

This project violates LinkedIn's User Agreement Section 8.2. As a result, LinkedIn may temporarily or permanently ban
your account. We are not responsible for any actions taken by LinkedIn in response to the use of this tool.

---
