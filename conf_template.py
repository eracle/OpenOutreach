import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logging.getLogger("scrapy").setLevel(logging.FATAL)

logging.getLogger("selenium").setLevel(logging.FATAL)

logging.getLogger("urllib3").setLevel(logging.FATAL)

EMAIL = ""
PASSWORD = ""
