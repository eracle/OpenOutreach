![built with Python3](https://img.shields.io/badge/built%20with-Python3-red.svg)
![built with Selenium](https://img.shields.io/badge/built%20with-Selenium-yellow.svg)

# LinkedIn Automation Tool

This tool is designed for automating tasks on LinkedIn. It leverages technologies such as Scrapy, Selenium WebDriver, Chromium, Docker, and Python3.

## Features

### LinkedIn Spider

The LinkedIn Spider is designed to visit as many LinkedIn user pages as possible. The goal is to increase the visibility of your account, as LinkedIn notifies users when their profile has been viewed.

### Companies Spider

The Companies Spider is designed to gather information about all users working for a specific company on LinkedIn. It operates by:

1. Navigating to the company's LinkedIn page.
2. Clicking on the "See all employees" button.
3. Collecting user-related data.

## Installation and Setup

You will need the following:

- Docker
- Docker Compose
- A VNC viewer (e.g., Vinagre for Ubuntu)


### Steps

1. **Prepare your environment**: Install Docker from the [official website](https://www.docker.com/). If you don't have a VNC viewer, install one. For Ubuntu, you can use Vinagre:

```bash
sudo apt-get update
sudo apt-get install vinagre
```

2. **Set up LinkedIn login and password**: Copy `conf_template.py` to `conf.py` and fill in your LinkedIn credentials.

3. **Run and build containers with Docker Compose**: Open your terminal, navigate to the project folder, and type:

```bash
make companies
or
make random
or
make byname
```

4. **Monitor the browser's activity**: Open Vinagre and connect to `localhost:5900`. The password is `secret`. Alternatively, you can use the command:

```bash
make view
```

5. **Stop the scraper**: To stop the scraper, use the command:

```bash
make down
```

## Testing

```bash
make test
```

## Legal Disclaimer

This code is not affiliated with, authorized, maintained, sponsored, or endorsed by LinkedIn or any of its affiliates or subsidiaries. This is an independent and unofficial project. Use at your own risk.

This project violates LinkedIn's User Agreement Section 8.2. As a result, LinkedIn may temporarily or permanently ban your account. We are not responsible for any actions taken by LinkedIn in response to the use of this tool.

---
