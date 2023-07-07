from scrapy.linkextractors import LinkExtractor
from scrapy.spiders import CrawlSpider
from scrapy.spiders import Rule

from linkedin.spiders.search import extract_contact_info
from linkedin.spiders.selenium import SeleniumSpiderMixin, get_by_xpath_or_none

"""
Variable holding where to search for first profiles to scrape.
"""
NETWORK_URL = 'https://www.linkedin.com/mynetwork/invite-connect/connections/'


class RandomSpider(SeleniumSpiderMixin, CrawlSpider):
    name = "random"
    start_urls = [
        NETWORK_URL,
    ]

    rules = (
        # Extract links matching a single user
        Rule(LinkExtractor(allow=('https:\/\/.*\/in\/\w*\/$',), deny=('https:\/\/.*\/in\/edit\/.*',)),
             callback='extract_profile_id_from_url',
             follow=True,
             ),
    )

    def wait_page_completion(self, driver):
        """
        Abstract function, used to customize how the specific spider have to wait for page completion.
        Blank by default
        :param driver:
        :return:
        """
        # waiting links to other users are shown so the crawl can continue
        get_by_xpath_or_none(driver, "//*[@id='global-nav']/div", wait_timeout=5)

    def extract_profile_id_from_url(self, response):
        # extract_profile_id_from_url
        profile_id = response.url.split('/')[-2]
        item = extract_contact_info(self.api_client, profile_id)

        driver = response.meta.pop('driver')
        driver.close()
        yield item
