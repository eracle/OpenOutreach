import pytest

from linkedin.spiders.companies import extracts_see_all_url

from tests.selenium import SeleniumTest

GOOGLE = "https://www.linkedin.com/company/google"
GOOGLE_USERS_LIST = "https://www.linkedin.com/search/results/people/?facetCurrentCompany=[%221441%22%2C%22621453%22%2C%22791962%22%2C%222374003%22%2C%2216140%22%2C%2210440912%22]"


@pytest.mark.skip
class CompaniesTest(SeleniumTest):
    def setUp(self):
        super().setUp()
        # login(self.driver)

    @pytest.mark.skip
    def test_extracts_see_all_url(self):
        self.driver.get(GOOGLE)
        url = extracts_see_all_url(self.driver)
        print(url)
        assert url.startswith(
            "https://www.linkedin.com/search/results/people/?facetCurrentCompany="
        )
