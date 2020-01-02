from scrapy.linkextractors import LinkExtractor
from scrapy.spiders import CrawlSpider
from scrapy.spiders import Rule

from linkedin.spiders.selenium import SeleniumSpiderMixin

NETWORK_URL = 'https://www.linkedin.com/mynetwork/invite-connect/connections/'


def extract_contact_info(api_client, contact_public_id):
    contact_profile = api_client.get_profile(contact_public_id)
    contact_info = api_client.get_profile_contact_info(contact_public_id)

    lastName = contact_profile['lastName']
    firstName = contact_profile['firstName']

    email_address = contact_info['email_address']
    phone_numbers = contact_info['phone_numbers']

    education = contact_profile['education']
    experience = contact_profile['experience']

    current_work = [exp for exp in experience if exp.get('timePeriod', {}).get('endDate') is None]

    return dict(lastName=lastName,
                firstName=firstName,
                email_address=email_address,
                phone_numbers=phone_numbers,
                education=education,
                experience=experience,
                current_work=current_work,
                )


class Linkedin(SeleniumSpiderMixin, CrawlSpider):
    name = "linkedin"
    start_urls = [
        NETWORK_URL,
    ]

    rules = (
        # Extract links matching a single user
        Rule(LinkExtractor(allow=('https:\/\/.*\/in\/\w*\/$',), deny=('https:\/\/.*\/in\/edit\/.*',)),
             callback='extract_profile_id_from_url',
             follow=True,
             ),
    )

    def extract_profile_id_from_url(self, response):
        # extract_profile_id_from_url
        profile_id = response.url.split('/')[-2]
        item = extract_contact_info(self.api_client, profile_id)

        yield item
