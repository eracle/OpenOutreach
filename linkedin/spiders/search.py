import logging

from scrapy import Request, Spider

from linkedin.integrations.linkedin_api import extract_profile_id_from_url
from linkedin.integrations.selenium import get_by_xpath_or_none
from linkedin.middlewares.selenium import SeleniumSpiderMixin

logger = logging.getLogger(__name__)


def increment_index_at_end_url(response):
    # incrementing the index at the end of the url
    url = response.request.url
    next_url_split = url.split("=")
    index = int(next_url_split[-1])
    next_url = "=".join(next_url_split[:-1]) + "=" + str(index + 1)
    return index, next_url


class SearchSpider(Spider, SeleniumSpiderMixin):
    """
    Abstract class for generic search on linkedin.
    """

    allowed_domains = ("linkedin.com",)

    def __init__(self, name=None, **kwargs):
        super().__init__(name, **kwargs)
        self.user_profile = None

    def parse_search_list(self, response):
        logger.debug("parse search list")
        driver = response.meta.pop("driver")
        no_result_found_xpath = (
            "//div[contains(@class, 'search-reusable-search-no-results')]"
        )
        no_result_response = get_by_xpath_or_none(
            driver=driver, xpath=no_result_found_xpath, wait_timeout=3
        )
        if no_result_response:
            # no results message shown: stop crawling this company
            logger.warning("no results message shown: stop crawling this company")
            return
        for user_profile_url in self.iterate_users(driver):
            self.user_profile = extract_profile_id_from_url(user_profile_url, driver.get_cookies())
            if not self.should_stop(response):
                yield self.user_profile
            else:
                break

        index, next_url = increment_index_at_end_url(response)

        yield Request(url=next_url, priority=-1, callback=self.parse_search_list, meta=response.meta)

    def wait_page_completion(self, driver):
        """
        Abstract function, used to customize how the specific spider must wait for a search page completion.
        """
        get_by_xpath_or_none(driver, "//*[@id='global-nav']/div", wait_timeout=5)

    def iterate_users(self, driver):
        for i in range(1, 11):
            self.sleep()
            container_xpath = f"//li[contains(@class, 'result-container')][{i}]"
            last_result = get_by_xpath_or_none(driver, container_xpath)
            if last_result:
                logger.debug(f"loading {i}th user")
                driver.execute_script("arguments[0].scrollIntoView();", last_result)
                # Use this XPath to select the <a> element
                link_elem = get_by_xpath_or_none(
                    last_result,
                    ".//a[contains(@class, 'app-aware-link') and contains(@href, '/in/')]",
                )
                if link_elem:
                    # Then extract the href attribute
                    user_url = link_elem.get_attribute("href")
                    logger.debug(f"Found user link:{user_url}")
                    yield user_url

    def should_stop(self, response):
        return False
