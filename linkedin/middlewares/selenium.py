"""This module contains the ``SeleniumMiddleware`` scrapy middleware"""

import logging
from random import uniform

from scrapy import signals
from scrapy.http import HtmlResponse
from selenium import webdriver
from twisted.internet import reactor
from twisted.internet.task import deferLater

from linkedin.integrations.selenium import selenium_login

logger = logging.getLogger(__name__)


class SeleniumSpiderMixin:
    def sleep(self, delay=None):
        randomize_delay = self.settings.getbool("RANDOMIZE_DOWNLOAD_DELAY")
        delay = delay or self.settings.getint("DOWNLOAD_DELAY")
        if randomize_delay:
            delay = uniform(0.5 * delay, 1.5 * delay)
        logger.debug(f"sleeping for {delay}")
        d = deferLater(reactor, delay, lambda: None)
        d.addErrback(lambda err: logger.error(err))
        return d


class SeleniumMiddleware:
    """Scrapy middleware handling the requests using selenium"""

    def __init__(self):
        SELENIUM_HOSTNAME = "selenium"
        selenium_url = f"http://{SELENIUM_HOSTNAME}:4444/wd/hub"

        chrome_options = webdriver.ChromeOptions()
        self.driver = webdriver.Remote(
            command_executor=selenium_url, options=chrome_options
        )
        selenium_login(self.driver)
        # self.cookies = self.driver.get_cookie()
        # logger.debug(f"Cookies: {self.cookies}")
        # add_cookies_to_selenium(self.cookies, self.driver)

    @classmethod
    def from_crawler(cls, crawler):
        """Initialize the middleware with the crawler settings"""
        instance = cls()
        crawler.signals.connect(instance.spider_closed, signals.spider_closed)
        return instance

    def process_request(self, request, spider):
        """Process a request using the selenium driver if applicable"""
        spider.sleep()
        self.driver.get(request.url)

        for cookie_name, cookie_value in request.cookies.items():
            self.driver.add_cookie({"name": cookie_name, "value": cookie_value})

        spider.wait_page_completion(self.driver)
        body = str.encode(self.driver.page_source)

        # Expose the driver via the "meta" attribute
        request.meta.update({"driver": self.driver})

        return HtmlResponse(
            self.driver.current_url, body=body, encoding="utf-8", request=request
        )

    def spider_closed(self):
        """Shutdown the driver when spider is closed"""
        self.driver.quit()
