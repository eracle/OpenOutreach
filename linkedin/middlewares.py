
from scrapy.http import HtmlResponse
from scrapy.utils.python import to_bytes

from linkedin.spiders.selenium import get_by_xpath, get_by_xpath_or_none


class SeleniumDownloaderMiddleware:

    def process_request(self, request, spider):
        driver = spider.driver

        print('SeleniumMiddleware - getting the page')
        driver.get(request.url)

        # request.meta['driver'] = self.driver  # to access driver from response

        print('waiting for page loading')
        profile_xpath = "//*[@id='nav-settings__dropdown-trigger']/img"
        get_by_xpath(driver, profile_xpath)

        # waiting links to other users are shown so the crawl can continue
        get_by_xpath_or_none(driver, '//*/span/span/span[1]', wait_timeout=3)

        print('SeleniumMiddleware - retrieving body')
        body = to_bytes(driver.page_source)  # body must be of type bytes

        return HtmlResponse(driver.current_url, body=body, encoding='utf-8', request=request)


