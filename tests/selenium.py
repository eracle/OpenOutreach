import unittest

import pytest

from linkedin.integrations.selenium import selenium_login


@pytest.mark.skip
class SeleniumTest(unittest.TestCase):
    def setUp(self):
        pass
        # self.driver = init_chromium('localhost')

    def tearDown(self):
        # pass
        self.driver.close()


class ChromiumTest(SeleniumTest):
    @pytest.mark.skip
    def test_init(self):
        self.assertIsNotNone(self.driver)
        print("type: %s" % type(self.driver))

    @pytest.mark.skip
    def test_selenium_login(self):
        selenium_login(self.driver)


if __name__ == "__main__":
    unittest.main()
