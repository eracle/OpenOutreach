# -*- coding: utf-8 -*-
from scrapy import Request
from scrapy.spiders import Spider

from linkedin.spiders.selenium import SeleniumSpiderMixin, extracts_see_all_url, extracts_linkedin_users, \
    get_by_xpath_or_none, wait_invisibility_xpath, extract_company

"""
Number of seconds to wait checking if the page is a "No Result" type.
"""
NO_RESULT_WAIT_TIMEOUT = 3

"""
First page to scrape from on the search results list (default to 1).
"""
FIRST_PAGE_INDEX = 1

URLS_FILE = "urls.txt"


class CompaniesSpider(SeleniumSpiderMixin, Spider):
    name = 'companies'
    allowed_domains = ['www.linkedin.com']

    with open(URLS_FILE, "rt") as f:
        start_urls = [url.strip() for url in f]

    def parse(self, response):
        url = extracts_see_all_url(self.driver) + f'&page={FIRST_PAGE_INDEX}'
        return Request(url=url,
                       callback=self.parser_search_results_page,
                       dont_filter=True,
                       )

    def parser_search_results_page(self, response):
        print('Now parsing search result page')

        no_result_found_xpath = '//*[text()="No results found."]'

        no_result_response = get_by_xpath_or_none(driver=self.driver,
                                                  xpath=no_result_found_xpath,
                                                  wait_timeout=NO_RESULT_WAIT_TIMEOUT,
                                                  logs=False)

        if no_result_response is not None:
            print('"No results" message shown, stop crawling this company')
            return
        else:
            company = extract_company(self.driver)
            print(f'Company:{company}')

            users = extracts_linkedin_users(self.driver, company=company)
            for user in users:
                yield user

            # incrementing the index at the end of the url
            url = response.request.url
            next_url_split = url.split('=')
            index = int(next_url_split[-1])
            next_url = '='.join(next_url_split[:-1]) + '=' + str(index + 1)

            yield Request(url=next_url,
                          callback=self.parser_search_results_page,
                          meta={'company': company},
                          dont_filter=True,
                          )
