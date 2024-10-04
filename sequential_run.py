import logging
import os
import time

from langchain_community.tools.slack import login
from scrapy.crawler import CrawlerRunner
from scrapy.utils.project import get_project_settings
from twisted.internet import defer, reactor

from linkedin.integrations.selenium import build_driver, is_security_check
from linkedin.spiders.companies import CompaniesSpider

# Define the number of seconds for the security check
SECURITY_CHECK_DURATION = 30
input_file_name = "/app/data/companies.txt"
output_file_name = f"/app/data/companies.csv"
logging.basicConfig(level=logging.DEBUG)


@defer.inlineCallbacks
def run_spiders_sequentially(runner, urls, driver):
    for url in urls:
        try:
            print("checking google.com")
            driver.get("https://www.google.com")
            assert "Google" in driver.title
        except Exception as e:
            print(e)
            driver = build_driver(login=True)
            perform_security_check(driver)
        yield runner.crawl(CompaniesSpider, start_url=url, driver=driver)
    yield driver.close()


def perform_security_check(driver):
    if is_security_check(driver):
        # Print instructions with fancy characters for user attention
        logging.info("***** SECURITY CHECK IN PROGRESS *****")
        logging.info(
            f"Please perform the security check on selenium, you have {SECURITY_CHECK_DURATION} seconds..."
        )

        for _ in range(SECURITY_CHECK_DURATION):
            time.sleep(1)

        logging.info("***** SECURITY CHECK COMPLETED *****")
    else:
        logging.debug("Security check not asked, continuing")


if __name__ == "__main__":
    print("Running companies scraper")

    settings = get_project_settings()
    settings.set('LOG_LEVEL', 'DEBUG')
    settings.set('LOG_ENABLED', True)
    settings.set('LOG_STDOUT', True)

    # Check if the input file exists before trying to read it
    if not os.path.isfile(input_file_name):
        logging.error(f"Input file {input_file_name} does not exist.")
        exit(1)

    # Try reading the input file and handle errors
    try:
        with open(input_file_name, "r", encoding="utf-8") as f:
            urls = [url.strip() for url in f if url.strip()]
    except Exception as e:
        logging.error(f"Failed to read input file {input_file_name}: {e}")
        exit(1)

    # Check if we have any URLs to process
    if not urls:
        logging.error(f"The input file {input_file_name} is empty or has invalid content.")
        exit(1)

    # Erase the past content of the output file
    open(output_file_name, "w").close()
    settings.set("FEEDS", {output_file_name: {"format": "csv"}})

    driver = build_driver(login=False)
    perform_security_check(driver)

    runner = CrawlerRunner(settings)

    # Run the spiders sequentially
    sequential_spiders = run_spiders_sequentially(runner, urls, driver)
    sequential_spiders.addBoth(
        lambda _: reactor.stop()
    )  # Stop the reactor when all spiders are done
    reactor.run()  # Start the reactor
