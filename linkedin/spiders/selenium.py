import logging

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as ec
from selenium.webdriver.support.wait import WebDriverWait

from conf import EMAIL, PASSWORD
from linkedin.integration import CustomLinkedinClient

logger = logging.getLogger(__name__)

"""
number of seconds used to wait the web page's loading.
"""
WAIT_TIMEOUT = 10

LINKEDIN_LOGIN_URL = 'https://www.linkedin.com/login'

""" Hostname used in the inter-comuication between docker instances,
from the scrapy controller to the selenium instance."""
SELENIUM_HOSTNAME = 'selenium'


class SeleniumSpiderMixin:
    """
    Abstract Spider based on Selenium.
    It takes care of login on linkedin.
    """

    def __init__(self, selenium_hostname=None, **kwargs):
        self.selenium_hostname = selenium_hostname or SELENIUM_HOSTNAME

        # initializing also API's client
        self.api_client = CustomLinkedinClient(EMAIL, PASSWORD, debug=True)

        # logging and saving cookies
        driver = init_chromium(self.selenium_hostname)
        self.cookies = login(driver)
        driver.close()

        super().__init__(**kwargs)

    def closed(self, reason):
        pass


def wait_invisibility_xpath(driver, xpath, wait_timeout=None):
    if wait_timeout is None:
        wait_timeout = WAIT_TIMEOUT

    WebDriverWait(driver, wait_timeout).until(ec.invisibility_of_element_located((By.XPATH, xpath)))


def get_by_xpath_or_none(driver, xpath, wait_timeout=None):
    """
    Get a web element through the xpath string passed.
    If a TimeoutException is raised the else_case is called and None is returned.
    :param driver: Selenium Webdriver to use.
    :param xpath: String containing the xpath.
    :param wait_timeout: optional amounts of seconds before TimeoutException is raised, default WAIT_TIMEOUT is used otherwise.
    :return: The web element or None if nothing found.
    """
    try:
        return get_by_xpath(driver, xpath, wait_timeout=wait_timeout)
    except (TimeoutException, StaleElementReferenceException, WebDriverException) as e:
        logging.warning(f"Current URL:\n{driver.current_url}\nException Occurred:\nXPATH: {xpath}\nError:{e}")
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


def init_chromium(selenium_host, cookies=None):
    selenium_url = f'http://{selenium_host}:4444/wd/hub'

    logger.debug(f'Initializing chromium, remote url: {selenium_url}')

    chrome_options = webdriver.ChromeOptions()
    driver = webdriver.Remote(
        command_executor=selenium_url,
        options=chrome_options
    )

    if cookies is not None:
        driver.get("https://www.linkedin.com/404error")
        for cookie in cookies:
            if 'expiry' in cookie:
                del cookie['expiry']
            driver.add_cookie(cookie)

    return driver


def login(driver):
    """
    Logs in in Linkedin.
    :param driver: The yet open selenium webdriver.
    :return: Nothing
    """
    driver.get(LINKEDIN_LOGIN_URL)

    logger.debug('Searching for the Login btn')
    get_by_xpath(driver, '//*[@id="username"]').send_keys(EMAIL)

    logger.debug('Searching for the password btn')
    get_by_xpath(driver, '//*[@id="password"]').send_keys(PASSWORD)

    logger.debug('Searching for the submit')
    get_by_xpath(driver, '//*[@type="submit"]').click()

    return driver.get_cookies()
