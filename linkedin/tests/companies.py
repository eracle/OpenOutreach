import pytest

from linkedin.spiders.selenium import login, extracts_see_all_url, extracts_linkedin_users, extract_company
from linkedin.tests.selenium import SeleniumTest

GOOGLE = 'https://www.linkedin.com/company/google'
GOOGLE_USERS_LIST = 'https://www.linkedin.com/search/results/people/?facetCurrentCompany=[%221441%22%2C%22621453%22%2C%22791962%22%2C%222374003%22%2C%2216140%22%2C%2210440912%22]'


class CompaniesTest(SeleniumTest):
    def setUp(self):
        super().setUp()
        login(self.driver)

    #@pytest.mark.skip
    def test_extracts_see_all_url(self):
        self.driver.get(GOOGLE)
        url = extracts_see_all_url(self.driver)
        print(url)
        assert url.startswith("https://www.linkedin.com/search/results/people/?facetCurrentCompany=")

    def test_extracts_linkedin_users(self):
        self.driver.get(GOOGLE_USERS_LIST)

        company = extract_company(self.driver)
        self.assertEquals(company, 'Google')

        users = extracts_linkedin_users(self.driver, company)
        self.assertEquals(len(list(users)), 10)
        for user in users:
            self.assertIsNotNone(user.get('name', None))
            self.assertIsNotNone(user.get('title', None))
            self.assertIsNotNone(user.get('company', None))
