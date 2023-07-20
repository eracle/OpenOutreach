import logging
from urllib.parse import urlencode

from scrapy import Request

from linkedin.spiders.search import SearchSpider

logger = logging.getLogger(__name__)

NAMES_FILE = "data/names.txt"
BASE_SEARCH_URL = "https://www.linkedin.com/search/results/people/"


class ByNameSpider(SearchSpider):
    """
    Spider who searches People by name.
    """

    name = "byname"

    def start_requests(self):
        with open(NAMES_FILE, "rt") as f:
            names = [line.rstrip() for line in f]
            if len(names) > 1:
                logger.warning(f"At the moment accepting only one name in {NAMES_FILE}, ignoring the rest")

            searched_name = names[0]
            logging.debug(f"encoded_name: {searched_name.lower()}")
            params = {
                "origin": "GLOBAL_SEARCH_HEADER",
                "keywords": searched_name.lower(),
                "page": 1
            }
            search_url = BASE_SEARCH_URL + "?" + urlencode(params)

            yield Request(
                url=search_url,
                callback=super().parse_search_list,
                meta={"searched_name": searched_name},
            )

    def should_stop(self, response):
        name_set = set(response.meta['searched_name'].lower().strip().split())

        last_name = self.user_profile["lastName"].lower().strip()
        first_name = self.user_profile["firstName"].lower().strip()
        user_name_set = set(last_name.split() + first_name.split())

        return not name_set == user_name_set
