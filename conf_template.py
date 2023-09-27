import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logging.getLogger("scrapy").setLevel(logging.FATAL)

logging.getLogger("selenium").setLevel(logging.FATAL)

logging.getLogger("urllib3").setLevel(logging.FATAL)

EMAIL = ""
PASSWORD = ""
# Keep it None to disable personalized Icebreakers generation
OPENAI_API_KEY = None

CONNECTION_REQUEST_LLM_PROMPT = """Act as a LinkedIn content creator reaching out to a professional on LinkedIn. 
Craft a connection request message referencing their past work experiences, showcasing that you've reviewed their 
profile, include specific details. Identify from their profile their primary language and write the message in that 
language. Do not include any line with subject or Primary language.
Do not include any templated info like your name and keep it under 300 characters.
Your output will be directly written into the linkedin textarea.

Professional's profile details:
{profile}"""

MAX_PROFILES_TO_SCRAPE = 200
MAX_PROFILES_TO_CONNECT = 20

# Feature Flag: SEND_CONNECTION_REQUESTS
# If set to True, the spider will send connection requests to LinkedIn profiles
# it visits during the scraping process. This can be useful for automating
# networking on LinkedIn, but use it with caution. Excessive connection requests
# can lead to your LinkedIn account being flagged or banned.
# If set to False, the spider will only scrape data without sending any connection requests.
SEND_CONNECTION_REQUESTS = True

# Feature Flag: SELECTIVE_SCRAPING
# If set to True, the scraper will ignore some profiles based on some role base filters
SELECTIVE_SCRAPING = True

# List of roles to select either in connection requests when
# SEND_CONNECTION_REQUESTS is enabled or simply to scrape and enrich
ROLES_FOR_CONNECTION_REQUESTS = [
    "CEO",
    "CTO",
    "CFO",
    "Project Manager",
]

DEFAULT_CONNECTION_MESSAGE = """
Hello!

I came across your profile and was impressed by your dedication in your field. 
I'm looking to connect with professionals like you who are working towards a better future. 
Would you like to connect and share perspectives?

Best regards
"""
