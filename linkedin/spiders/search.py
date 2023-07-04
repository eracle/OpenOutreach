import copy
import logging
import time

from scrapy import Request
from scrapy import Spider

from linkedin.spiders.selenium import get_by_xpath_or_none, SeleniumSpiderMixin


"""
Number of seconds to wait checking if the page is a "No Result" type.
"""
NO_RESULT_WAIT_TIMEOUT = 3


class SearchSpider(SeleniumSpiderMixin, Spider):
    """
    Abstract class for generic search on linkedin.
    """

    def wait_page_completion(self, driver):
        """
        Abstract function, used to customize how the specific spider must wait for a search page completion.
        Blank by default
        :param driver:
        :return:
        """
        # profile_xpath = "//*[@id='nav-settings__dropdown-trigger']/img"
        # get_by_xpath_or_none(driver, profile_xpath)
        pass

    def parser_search_results_page(self, response):
        # getting optional callback's arguments:
        driver = response.meta.pop('driver')

        # maximum number for pagination
        max_page = response.meta.get('max_page', None)

        # stop_criteria : returns True if search must stop
        stop_criteria = response.meta.get('stop_criteria', None)
        stop_criteria_args = response.meta.get('stop_criteria_args', None)

        # Now parsing search result page
        no_result_found_xpath = '//*[text()="No results found."]'
        no_result_response = get_by_xpath_or_none(driver=driver,
                                                  xpath=no_result_found_xpath,
                                                  wait_timeout=NO_RESULT_WAIT_TIMEOUT)

        if no_result_response is not None:
            # no results message shown: stop crawling this company
            logging.warning("no results message shown: stop crawling this company")
            driver.close()
            return
        else:
            users = extracts_linkedin_users(driver, api_client=self.api_client)
            for user in users:
                if stop_criteria is not None and stop_criteria(user, stop_criteria_args):
                    # if stop criteria is matched stops the crawl, and also next pages
                    logging.warning("stop criteria is matched stops the crawl, and also next pages")
                    driver.close()
                    return
                else:
                    yield user

            index, next_url = self.increment_index_at_end_url(response)

            if max_page is not None and index >= max_page:
                logging.warning("index >= max_page: stops the crawl")
                driver.close()
                return

            driver.close()
            yield Request(url=next_url,
                          callback=self.parser_search_results_page,
                          meta=copy.deepcopy(response.meta),
                          dont_filter=True,
                          )

    def increment_index_at_end_url(self, response):
        # incrementing the index at the end of the url
        url = response.request.url
        next_url_split = url.split('=')
        index = int(next_url_split[-1])
        next_url = '='.join(next_url_split[:-1]) + '=' + str(index + 1)
        return index, next_url


######################
# Module's functions:
######################
def extracts_linkedin_users(driver, api_client):
    """
    Gets from a page containing a list of users, all the users.
    For instance: https://www.linkedin.com/search/results/people/?facetCurrentCompany=[%22221027%22]
    :param driver: The webdriver, logged in, and located in the page which lists users.
    :return: Iterator on LinkedinUser.
    """

    for i in range(1, 11):
        print(f'loading {i}th user')

        last_result_xpath = f'//li[{i}]/*/div[@class="search-result__wrapper"]'

        result = get_by_xpath_or_none(driver, last_result_xpath)
        if result is not None:
            link_elem = get_by_xpath_or_none(result, './/*[@class="search-result__result-link ember-view"]')
            link = link_elem.get_attribute('href') if link_elem is not None else None

            name_elem = get_by_xpath_or_none(result, './/*[@class="name actor-name"]')
            name = name_elem.text if name_elem is not None else None

            title_elem = get_by_xpath_or_none(result, './/p')
            title = title_elem.text if name_elem is not None else None

            # extract_profile_id_from_url
            profile_id = link.split('/')[-2]
            user = extract_contact_info(api_client, profile_id)

            yield user

            if link_elem is not None:
                driver.execute_script("arguments[0].scrollIntoView();", link_elem)
            elif name_elem is not None:
                driver.execute_script("arguments[0].scrollIntoView();", name_elem)
            elif title_elem is not None:
                driver.execute_script("arguments[0].scrollIntoView();", title_elem)
            else:
                print("Was not possible to scroll")

        time.sleep(0.7)


def filter_istr_dict(elem):
    wanted_istr = {'schoolName', 'degreeName', 'fieldOfStudy', 'timePeriod',
                   # 'description',
                   'grade'}
    return dict([(k, v) for k, v in elem.items() if k in wanted_istr])


def filter_experience_dict(elem):
    wanted_experience = {'companyName', 'industries', 'title', 'startDate', 'timePeriod', 'geoLocationName',
                         # 'description',
                         'locationName', 'company', }
    return dict([(k, v) for k, v in elem.items() if k in wanted_experience])


def extract_contact_info(api_client, contact_public_id):
    contact_profile = api_client.get_profile(contact_public_id)
    contact_info = api_client.get_profile_contact_info(contact_public_id)

    lastName = contact_profile['lastName']
    firstName = contact_profile['firstName']

    email_address = contact_info['email_address']
    phone_numbers = contact_info['phone_numbers']

    education = list(map(filter_istr_dict, contact_profile['education']))
    experience = list(map(filter_experience_dict, contact_profile['experience']))

    # current_work = [exp for exp in experience if exp.get('timePeriod', {}).get('endDate') is None]

    return dict(lastName=lastName,
                firstName=firstName,
                email_address=email_address,
                phone_numbers=phone_numbers,
                education=education,
                experience=experience,
                # current_work=current_work,
                )
