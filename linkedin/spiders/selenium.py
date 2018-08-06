import time

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException, WebDriverException
from selenium.webdriver import DesiredCapabilities
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as ec
from selenium.webdriver.support.wait import WebDriverWait

from conf import EMAIL, PASSWORD
from linkedin.items import LinkedinUser

"""
number of seconds used to wait the web page's loading.
"""
WAIT_TIMEOUT = 10

LINKEDIN_LOGIN_URL = 'https://www.linkedin.com/'

""" Hostname used in the inter-comuication between docker instances,
from the scrapy controller to the selenium instance."""
SELENIUM_HOSTNAME = 'selenium'

"""
Placeholder used to recognize the 'See all 27,569 employees on LinkedIn' clickable button,
in the 'https://www.linkedin.com/company/*/' style pages.
"""
SEE_ALL_PLACEHOLDER = 'See all'


def wait_invisibility_xpath(driver, xpath, wait_timeout=None):
    if wait_timeout is None:
        wait_timeout = WAIT_TIMEOUT

    WebDriverWait(driver, wait_timeout).until(ec.invisibility_of_element_located((By.XPATH, xpath)))


def get_by_xpath_or_none(driver, xpath, wait_timeout=None, logs=True):
    """
    Get a web element through the xpath string passed.
    If a TimeoutException is raised the else_case is called and None is returned.
    :param driver: Selenium Webdriver to use.
    :param xpath: String containing the xpath.
    :param wait_timeout: optional amounts of seconds before TimeoutException is raised, default WAIT_TIMEOUT is used otherwise.
    :param logs: optional, prints a status message to stdout if an exception occures.
    :return: The web element or None if nothing found.
    """
    try:
        return get_by_xpath(driver, xpath, wait_timeout=wait_timeout)
    except (TimeoutException, StaleElementReferenceException, WebDriverException) as e:
        if logs:
            print("Exception Occurred:")
            print(f"XPATH:{xpath}")
            print(f"Error:{e}")
        return None


def get_by_xpath(driver, xpath, wait_timeout=None):
    """
    Get a web element through the xpath passed by performing a Wait on it.
    :param driver: Selenium web driver to use.
    :param xpath: xpath to use.
    :param wait_timeout: optional amounts of seconds before TimeoutException is raised, default WAIT_TIMEOUT is used otherwise.
    :return: The web element.
    """
    if wait_timeout is None:
        wait_timeout = WAIT_TIMEOUT
    return WebDriverWait(driver, wait_timeout).until(
        ec.presence_of_element_located(
            (By.XPATH, xpath)
        ))


def init_chromium(selenium_host):
    selenium_url = 'http://%s:4444/wd/hub' % selenium_host

    print('Initializing chromium, remote url: %s' % selenium_url)

    chrome_options = DesiredCapabilities.CHROME
    # chrome_options.add_argument('--disable-notifications')

    prefs = {"credentials_enable_service": False, "profile.password_manager_enabled": False}

    chrome_options['prefs'] = prefs

    driver = webdriver.Remote(command_executor=selenium_url,
                              desired_capabilities=chrome_options)
    return driver


def login(driver):
    """
    Logs in in Linkedin.
    :param driver: The yet open selenium webdriver.
    :return: Nothing
    """
    driver.get(LINKEDIN_LOGIN_URL)

    print('Searching for the Login btn')
    get_by_xpath(driver, '//*[@class="login-email"]').send_keys(EMAIL)

    print('Searching for the password btn')
    get_by_xpath(driver, '//*[@class="login-password"]').send_keys(PASSWORD)

    print('Searching for the submit')
    get_by_xpath(driver, '//*[@id="login-submit"]').click()


def extracts_see_all_url(driver):
    """
    Retrieve from the the Company front page the url of the page containing the list of its employees.
    :param driver: The already opened (and logged in) webdriver, already located to the company's front page.
    :return: String: The "See All" URL.
    """
    print('Searching for the "See all * employees on LinkedIn" btn')
    see_all_xpath = f'//a/strong[starts-with(text(),"{SEE_ALL_PLACEHOLDER}")]'
    see_all_elem = get_by_xpath(driver, see_all_xpath)
    see_all_ex_text = see_all_elem.text

    a_elem = driver.find_element_by_link_text(see_all_ex_text)
    see_all_url = a_elem.get_attribute('href')
    print(f'Found the following URL: {see_all_url}')
    return see_all_url


def extracts_linkedin_users(driver, company):
    """
    Gets from a page containing a list of users, all the users.
    For instance: https://www.linkedin.com/search/results/people/?facetCurrentCompany=[%22221027%22]
    :param driver: The webdriver, logged in, and located in the page which lists users.
    :return: Iterator on LinkedinUser.
    """

    for i in range(1, 11):
        print(f'loading {i}th user')

        last_result_xpath = f'//li[{i}]/div/div[@class="search-result__wrapper"]'

        result = get_by_xpath_or_none(driver, last_result_xpath)
        if result is not None:


            link_elem = get_by_xpath_or_none(result, './/*[@class="search-result__result-link ember-view"]')
            link = link_elem.get_attribute('href') if link_elem is not None else None

            name_elem = get_by_xpath_or_none(result, './/*[@class="name actor-name"]')
            name = name_elem.text if name_elem is not None else None

            title_elem = get_by_xpath_or_none(result, './/p')
            title = title_elem.text if name_elem is not None else None

            user = LinkedinUser(name=name, title=title, company=company, link=link)

            yield user

            focus_elem_xpath = './/figure[@class="search-result__image"]/img'
            focus_elem = get_by_xpath_or_none(result, focus_elem_xpath, wait_timeout=1)
            if focus_elem is not None:
                driver.execute_script("arguments[0].scrollIntoView();", focus_elem)
        time.sleep(0.7)


def extract_company(driver):
    """
    Extract company name from a search result page.
    :param driver: The selenium webdriver.
    :return: The company string, None if something wrong.
    """
    company_xpath = '//li[@class="search-s-facet search-s-facet--facetCurrentCompany inline-block ' \
                    'search-s-facet--is-closed ember-view"]/form/button/div/div/h3 '
    company_elem = get_by_xpath_or_none(driver, company_xpath)
    return company_elem.text if company_elem is not None else None


class SeleniumSpiderMixin:
    def __init__(self, selenium_hostname=None, **kwargs):
        if selenium_hostname is None:
            selenium_hostname = SELENIUM_HOSTNAME

        self.driver = init_chromium(selenium_hostname)

        # Stop web page from asking me if really want to leave - past implementation, FIREFOX
        # profile = webdriver.FirefoxProfile()
        # profile.set_preference('dom.disable_beforeunload', True)
        # self.driver = webdriver.Firefox(profile)

        login(self.driver)

        super().__init__(**kwargs)

    def closed(self, reason):
        self.driver.close()
