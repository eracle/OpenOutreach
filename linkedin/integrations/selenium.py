import logging

from selenium.common import (StaleElementReferenceException, TimeoutException,
                             WebDriverException)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as ec
from selenium.webdriver.support.wait import WebDriverWait

from conf import EMAIL, PASSWORD

logger = logging.getLogger(__name__)
"""
Number of seconds used to wait the web page's loading.
"""
WAIT_TIMEOUT = 10

LINKEDIN_LOGIN_URL = "https://www.linkedin.com/login"


def selenium_login(driver):
    """
    Logs in in Linkedin.
    :param driver: The yet open selenium webdriver.
    :return: Nothing
    """
    driver.get(LINKEDIN_LOGIN_URL)

    logger.debug("Searching for the Login btn")
    get_by_xpath(driver, '//*[@id="username"]').send_keys(EMAIL)

    logger.debug("Searching for the password btn")
    get_by_xpath(driver, '//*[@id="password"]').send_keys(PASSWORD)

    logger.debug("Searching for the submit")
    get_by_xpath(driver, '//*[@type="submit"]').click()


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
        ec.presence_of_element_located((By.XPATH, xpath))
    )


def get_by_xpath_or_none(driver, xpath, wait_timeout=None, log=False):
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
    except (TimeoutException, StaleElementReferenceException) as e:
        logger.info(
            f"Current URL:\n{driver.current_url}\nTimeoutException:\nXPATH: {xpath}\nError:{e}"
        ) if log else None
    except WebDriverException as e:
        if hasattr(driver, "current_url"):
            logger.warning(f"Current URL:\n{driver.current_url}")
        logger.warning(f"WebDriverException:\nXPATH: {xpath}\nError:{e}")
