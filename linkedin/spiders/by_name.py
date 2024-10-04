import logging
import os
from urllib.parse import urlencode

from scrapy import Request

from linkedin.spiders.search import SearchSpider

logger = logging.getLogger(__name__)

NAMES_FILE = "/app/data/names.txt"
BASE_SEARCH_URL = "https://www.linkedin.com/search/results/people/"

class ByNameSpider(SearchSpider):
    """
    Spider who searches People by name.
    """

    name = "byname"

    def __init__(self, *args, **kwargs):
        # Initialize SearchSpider with a default start_url
        start_url = BASE_SEARCH_URL
        super().__init__(start_url=start_url, *args, **kwargs)

    def start_requests(self):
        # Check if the file exists before trying to read it
        if not os.path.isfile(NAMES_FILE):
            logger.error(f"Names file {NAMES_FILE} not found. Please ensure the file exists.")
            return  # Stop execution if the file is missing

        # Read the names from the file and handle empty files
        with open(NAMES_FILE, "rt") as f:
            names = [line.rstrip() for line in f if line.strip()]  # Ignore empty lines

        if not names:
            logger.error(f"Names file {NAMES_FILE} is empty. Please provide at least one name.")
            return  # Stop execution if the file is empty

        # Limit to the first name if there are multiple
        if len(names) > 1:
            logger.warning(
                f"At the moment accepting only one name in {NAMES_FILE}, ignoring the rest"
            )

        searched_name = names[0]
        logger.debug(f"encoded_name: {searched_name.lower()}")
        params = {
            "origin": "GLOBAL_SEARCH_HEADER",
            "keywords": searched_name.lower(),
            "page": 1,
        }
        search_url = BASE_SEARCH_URL + "?" + urlencode(params)

        yield Request(
            url=search_url,
            callback=super().parse_search_list,
            meta={"searched_name": searched_name},
        )

    def should_stop(self, response):
        name_set = set(response.meta["searched_name"].lower().strip().split())

        last_name = self.user_profile["lastName"].lower().strip()
        first_name = self.user_profile["firstName"].lower().strip()
        user_name_set = set(last_name.split() + first_name.split())
        should_stop = not name_set == user_name_set

        return super().should_stop(response) and should_stop
