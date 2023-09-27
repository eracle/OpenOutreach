import logging
from time import sleep

from langchain.llms import OpenAI
from scrapy import Request, Spider
from selenium import webdriver
from selenium.webdriver.common.keys import Keys

from conf import (
    CONNECTION_REQUEST_LLM_PROMPT,
    DEFAULT_CONNECTION_MESSAGE,
    MAX_PROFILES_TO_CONNECT,
    MAX_PROFILES_TO_SCRAPE,
    OPENAI_API_KEY,
    ROLES_KEYWORDS,
    SELECTIVE_SCRAPING,
    SEND_CONNECTION_REQUESTS,
)
from linkedin.integrations.linkedin_api import extract_profile_from_url
from linkedin.integrations.selenium import get_by_xpath_or_none
from linkedin.items import LinkedinUser
from linkedin.middlewares.selenium import SeleniumSpiderMixin

logger = logging.getLogger(__name__)

SLEEP_TIME_BETWEEN_CLICKS = 1.5

roles_keywords_lowercase = [role.lower() for role in ROLES_KEYWORDS]


def remove_non_bmp_characters(text):
    return "".join(c for c in text if 0x0000 <= ord(c) <= 0xFFFF)


def remove_primary_language(text):
    lines = text.split("\n")
    filtered_lines = [line for line in lines if "primary language" not in line.lower()]
    return "\n".join(filtered_lines)


def is_your_network_is_growing_present(driver):
    got_it_button = get_by_xpath_or_none(
        driver,
        '//button[@aria-label="Got it"]',
        wait_timeout=0.5,
    )
    return got_it_button is not None


def is_email_verifier_present(driver):
    email_verifier = get_by_xpath_or_none(
        driver,
        "//label[@for='email']",
        wait_timeout=0.5,
    )
    return email_verifier is not None


def send_connection_request(driver, message):
    sleep(SLEEP_TIME_BETWEEN_CLICKS)

    # Click the "Add a note" button
    add_note_button = get_by_xpath_or_none(
        driver,
        "//button[contains(@aria-label, 'note')]",
    )
    click(driver, add_note_button) if add_note_button else logger.warning(
        "Add note button unreachable"
    )
    sleep(SLEEP_TIME_BETWEEN_CLICKS)

    # Write the message in the textarea
    message_textarea = get_by_xpath_or_none(
        driver,
        "//textarea[@name='message' and @id='custom-message']",
    )
    message_textarea.send_keys(message[:300]) if message_textarea else logger.warning(
        "Textarea unreachable"
    )
    sleep(SLEEP_TIME_BETWEEN_CLICKS)

    # Click the "Send" button
    send_button = get_by_xpath_or_none(
        driver,
        "//button[@aria-label='Send now']",
    )
    click(driver, send_button) if send_button else logger.warning(
        "Send button unreachable"
    )
    sleep(SLEEP_TIME_BETWEEN_CLICKS)
    return True


def skip_connection_request(connect_button):
    return not (connect_button and SEND_CONNECTION_REQUESTS)


def contains_keywords(user_profile):
    headline = user_profile["headline"].lower()
    return any(role in headline for role in roles_keywords_lowercase)


def skip_profile(user_profile):
    return SELECTIVE_SCRAPING and not contains_keywords(user_profile)


def generate_connection_message(llm: OpenAI, user_profile):
    from langchain import PromptTemplate

    prompt_template = PromptTemplate.from_template(CONNECTION_REQUEST_LLM_PROMPT)

    prompt = prompt_template.format(profile=user_profile)
    logger.debug(f"Generate message with prompt:\n{prompt}:")
    msg = llm.predict(prompt).strip()
    msg = remove_primary_language(msg).strip()
    msg = remove_non_bmp_characters(msg).strip()
    logger.info(f"Generated Icebreaker:\n{msg}")
    return msg


def extract_connect_button(user_container):
    connect_button = get_by_xpath_or_none(
        user_container,
        ".//button[contains(@aria-label, 'connect')]/span",
        wait_timeout=5,
    )
    return (
        connect_button if connect_button else logger.debug("Connect button not found")
    )


def increment_index_at_end_url(response):
    # incrementing the index at the end of the url
    url = response.request.url
    next_url_split = url.split("=")
    index = int(next_url_split[-1])
    next_url = "=".join(next_url_split[:-1]) + "=" + str(index + 1)
    return index, next_url


def extract_user_url(user_container):
    # Use this XPath to select the <a> element
    link_elem = get_by_xpath_or_none(
        user_container,
        ".//a[contains(@class, 'app-aware-link') and contains(@href, '/in/')]",
    )

    if not link_elem:
        logger.warning("Can't extract user URL")
        return None

    user_url = link_elem.get_attribute("href")
    logger.debug(f"Extracted user URL: {user_url}")
    return user_url


def click(driver, element):
    driver.execute_script("arguments[0].scrollIntoView();", element)
    driver.execute_script("arguments[0].click();", element)


def press_exit(driver):
    webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()


class SearchSpider(Spider, SeleniumSpiderMixin):
    """
    Abstract class for generic search on linkedin.
    """

    allowed_domains = ("linkedin.com",)

    def __init__(self, name=None, **kwargs):
        super().__init__(name, **kwargs)
        self.user_profile = None
        self.profile_counter = 0
        self.connections_sent_counter = 0
        self.llm = (
            OpenAI(
                max_tokens=90,
                model_name="text-davinci-003",
                openai_api_key=OPENAI_API_KEY,
            )
            if SEND_CONNECTION_REQUESTS
            else None
        )

    def wait_page_completion(self, driver):
        """
        Abstract function, used to customize how the specific spider must wait for a search page completion.
        """
        get_by_xpath_or_none(driver, "//*[@id='global-nav']/div", wait_timeout=5)

    def parse_search_list(self, response):
        continue_scrape = True
        driver = self.get_driver_from_response(response)
        if self.check_if_no_results_found(driver):
            logger.warning("No results found. Stopping crawl.")
            return

        for user_container in self.iterate_containers(driver):
            if is_your_network_is_growing_present(driver):
                press_exit(driver)
            user_profile_url = extract_user_url(user_container)
            if user_profile_url is None:
                continue
            logger.debug(f"Found user URL:{user_profile_url}")
            self.user_profile = extract_profile_from_url(
                user_profile_url, driver.get_cookies()
            )
            if self.should_stop(response):
                continue_scrape = False
                break

            connect_button = extract_connect_button(user_container)
            if skip_profile(self.user_profile):
                logger.info(f"Skipped profile: {user_profile_url}")
            else:
                message = (
                    generate_connection_message(self.llm, self.user_profile)
                    if OPENAI_API_KEY
                    else DEFAULT_CONNECTION_MESSAGE
                )
                self.user_profile["connection_msg"] = (
                    message if OPENAI_API_KEY else None
                )
                if skip_connection_request(connect_button):
                    logger.info(f"Skipped connection request: {user_profile_url}")
                else:
                    click(driver, connect_button)
                    if is_email_verifier_present(driver):
                        press_exit(driver)
                    else:
                        conn_sent = send_connection_request(driver, message=message)
                        logger.info(
                            f"Connection request sent to {user_profile_url}\n{message}"
                        ) if conn_sent else None
                        self.connections_sent_counter += 1

                yield LinkedinUser(linkedinUrl=user_profile_url, **self.user_profile)
                self.profile_counter += 1

        if continue_scrape:
            next_url = self.get_next_url(response)
            yield self.create_next_request(next_url, response)

    def get_driver_from_response(self, response):
        return response.meta.pop("driver")

    def check_if_no_results_found(self, driver):
        no_result_found_xpath = (
            "//div[contains(@class, 'search-reusable-search-no-results')]"
        )
        return (
            get_by_xpath_or_none(
                driver=driver, xpath=no_result_found_xpath, wait_timeout=3
            )
            is not None
        )

    def get_next_url(self, response):
        index, next_url = increment_index_at_end_url(response)
        return next_url

    def create_next_request(self, next_url, response):
        return Request(
            url=next_url,
            priority=-1,
            callback=self.parse_search_list,
            meta=response.meta,
        )

    def iterate_containers(self, driver):
        for i in range(1, 11):
            container_xpath = f"//li[contains(@class, 'result-container')][{i}]"
            container_elem = get_by_xpath_or_none(
                driver, container_xpath, wait_timeout=2
            )
            if container_elem:
                logger.debug(f"Loading {i}th user")
                driver.execute_script("arguments[0].scrollIntoView();", container_elem)
                self.sleep()
                yield container_elem

    def should_stop(self, response):
        max_num_profiles = self.profile_counter >= MAX_PROFILES_TO_SCRAPE
        if max_num_profiles:
            logger.info(
                "Stopping Reached maximum number of profiles to scrape. Stopping crawl."
            )

        max_num_connections = self.connections_sent_counter >= MAX_PROFILES_TO_CONNECT
        if max_num_connections:
            logger.info(
                "Stopping Reached maximum number of profiles to connect. Stopping crawl."
            )

        return max_num_profiles
