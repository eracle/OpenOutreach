from scrapy.linkextractors import LinkExtractor
from scrapy.spiders import CrawlSpider
from scrapy.spiders import Rule

from linkedin.spiders.selenium import SeleniumSpiderMixin

NETWORK_URL = 'https://www.linkedin.com/mynetwork/invite-connect/connections/'


class Linkedin(SeleniumSpiderMixin, CrawlSpider):
    name = "linkedin"
    start_urls = [
        NETWORK_URL,
    ]

    rules = (
        # Extract links matching a single user
        Rule(LinkExtractor(allow=('https:\/\/.*\/in\/\w*\/$',), deny=('https:\/\/.*\/in\/edit\/.*',)),
             follow=True,
             ),
    )
