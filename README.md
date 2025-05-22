## Sponsors:
Unlock Reliable Proxy Services with Swiftproxy
With Swiftproxy, you can access high-performance, secure proxies to enhance your web automation, privacy, and data collection efforts. Our services are trusted by developers and businesses to scale scraping tasks and ensure a safe online experience. Get started today at Swiftproxy.net.
Additionally, feel free to mention that anyone who signs up can use the discount code GHB5 to get 10% off their purchase at checkout!
[![Scrap.io Banner](docs/scrapio.webp)](https://scrap.id/s/91BR)

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=eracle/linkedin&type=Date)](https://star-history.com/#eracle/linkedin&Date)

# LinkedIn Data Scraper

![built with Python3](https://img.shields.io/badge/built%20with-Python3-red.svg)
![built with Selenium](https://img.shields.io/badge/built%20with-Selenium-yellow.svg)

LinkedIn Data Scraper is a powerful open-source tool designed to extract valuable data from LinkedIn. It leverages technologies such as Scrapy, Selenium WebDriver, Chromium, Docker, and Python3 to navigate LinkedIn profiles and gather insightful information.

## Features

### Profile Data Extraction

The tool is designed to visit LinkedIn user pages and extract valuable data. This includes phone numbers, emails, education, work experiences, and much more. The data is formatted in a CSV file, making it easy to use for further analysis or input for LinkedIn automation software like lemlist.

### Company Data Extraction

The tool can also gather information about all users working for a specific company on LinkedIn. It navigates to the company's LinkedIn page, clicks on the "See all employees" button, and collects user-related data.

### Name-Based Data Extraction

The tool also offers a unique feature that allows you to extract data based on a specific name. By having the name of a person on the `names.txt` file, the tool will navigate to the LinkedIn profiles associated with that name and extract the relevant data. This feature can be incredibly useful for targeted research or networking. To use this feature, simply use the `make byname` command and input the name when prompted.

### Custom Connection Messages using ChatGPT API

The software can be configured to send custom connection messages to LinkedIn users using the ChatGPT API. By passing in the person's profile details, the tool can craft a personalized message that is highly tailored and relevant, significantly increasing the acceptance rate of connection requests. This feature leverages OpenAI's language model to analyze the profile and generate a message that reflects the user's work experiences, skills, and primary language, making outreach more effective.

## Installation and Setup

You will need the following:

- Docker, I am using version  27.3.1, be sure you have docker compose
- A VNC viewer (e.g., Vinagre for Ubuntu)

### Steps

1. **Prepare your environment**: Install Docker from the [official website](https://www.docker.com/). If you don't have a VNC viewer, install one. For Ubuntu, you can use Vinagre:

```bash
sudo apt-get update
sudo apt-get install vinagre
```

2. **Set up LinkedIn login and password**: Copy `conf_template.py` to `conf.py` and fill in your LinkedIn credentials.

```bash
cp conf_template.py conf.py

cat <<EOL >> conf.py
EMAIL = "YOUR LINKEDIN EMAIL"
PASSWORD = "YOUR LINKEDIN PASSWORD"
EOL
```

4. **Run and build containers with Docker Compose**: Open your terminal, navigate to the project folder, and type:

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
## Newly created Telegram Group
Used by the Mantainer to [receive feedback or discuss new ideas](https://t.me/+Y5bh9Vg8UVg5ODU0).
