“Linkedin is being sponsored by the following tool; please help to support us by taking a look and signing up to a free trial”
<a href="https://tracking.gitads.io/?repo=linkedin"> <img src="https://images.gitads.io/linkedin" alt="GitAds" width="100" /> </a>
[![built with Python3](https://img.shields.io/badge/built%20with-Python3-red.svg)](https://www.python.org/)
[![built with Selenium](https://img.shields.io/badge/built%20with-Selenium-yellow.svg)](https://github.com/SeleniumHQ/selenium)

# Linkedin Automation:

Uses: Scrapy, Selenium web driver, Chromium headless, docker and python3.

#### Linkedin spider:
The first spider aims to visit as more linkedin's user pages as possible :-D, the objective is to gain visibility with your account: since LinkedIn notifies the issued User when someone visits his page.

#### Companies spider:
This spider aims to collect all the users working for a company on linkedin.
1. It goes on the company's front-page;
2. Clicks on "See all 1M employees" button;
3. Starts collecting User related Scapy items.


### Install
Needed:
- docker;
- docker-compose;
- VNC viewer, like vinagre (ubuntu);
- python3.6;
- virtualenvs;

###### 0. Prepare your environment:

Install docker from the official website [https://www.docker.com/](https://www.docker.com/)

Install VNC viewer if you do not have one. 
For ubuntu, go for vinagre:

```bash
sudo apt-get update
sudo apt-get install vinagre
```

###### 1. Set up Linkedin login and password:
Copy `conf_template.py` in `conf.py` and fill the quotes with your credentials.

###### 2. Run and build containers with docker-compose:
Only linkedin random spider, not the companies spider.
Open your terminal, move to the project folder and type:

```bash
docker-compose up -d --build
```


###### 3. Take a look on the browser's activity:

Open vinagre, and type address and port `localhost:5900`. The password is `secret`.
or otherwise:
```bash
vinagre localhost:5900
or
make view
```

###### 4. Stop the scraper;

Use your terminal again, type in the same window:

```bash
docker-compose down
```


###### Test & Development:
Setup your python virtual environment (trivial but mandatory):

```bash
    virtualenvs -p python3.6 .venv
    source .venv/bin/activate
    pip install -r requirements.txt
```

Create the selenium server, open the VNC window and launch the tests, type those in three different terminals on the project folder:
```bash
    make dev
    make view
    make tests
```

For more details have a look at the Makefile (here is used to shortcut and not to build).
- Development:
```bash
    scrapy crawl companies -a selenium_hostname=localhost -o output.csv
```
or
```bash
    scrapy crawl random -a selenium_hostname=localhost -o output.csv
```
or
```bash
    scrapy crawl byname -a selenium_hostname=localhost -o output.csv
```
## Legal

This code is in no way affiliated with, authorized, maintained, sponsored or endorsed by Linkedin or any of its affiliates or subsidiaries. This is an independent and unofficial project. Use at your own risk.

This project violates Linkedin's User Agreement Section 8.2, and because of this, Linkedin may (and will) temporarily or permantly ban your account. We are not responsible for your account being banned.
