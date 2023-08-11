import logging
import multiprocessing

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from linkedin.spiders.companies import CompaniesSpider  # replace with your actual spider


def run_spider(url):
    logging.debug(f"Starting process for {url}")
    # Extract company name from the URL and use it as the file name
    company_name = url.strip().rstrip("/").split('/')[-1]
    file_name = f"data/companies/{company_name}.csv"

    # Erase the past content of the file
    open(file_name, 'w').close()

    settings = get_project_settings()
    settings.set('FEEDS', {file_name: {'format': 'csv'}})

    process = CrawlerProcess(settings)
    process.crawl(CompaniesSpider, start_url=url)
    process.start()


if __name__ == "__main__":
    with open('data/urls.txt', 'r') as file:
        for url in file:
            url = url.strip()  # remove newline characters
            p = multiprocessing.Process(target=run_spider, args=(url,))
            p.start()
            p.join()  # this line will block until the spider is finished
