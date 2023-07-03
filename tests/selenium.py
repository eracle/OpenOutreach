import unittest

import pytest

from linkedin.spiders.selenium import init_chromium, login


class SeleniumTest(unittest.TestCase):

    def setUp(self):
        self.driver = init_chromium('localhost')

    def tearDown(self):
        # pass
        self.driver.close()


class ChromiumTest(SeleniumTest):

    @pytest.mark.skip
    def test_init(self):
        self.assertIsNotNone(self.driver)
        print("type: %s" % type(self.driver))

    @pytest.mark.skip
    def test_login(self):
        login(self.driver)


if __name__ == '__main__':
    unittest.main()
