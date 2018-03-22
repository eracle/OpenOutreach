import unittest

from linkedin.spiders.selenium import init_chromium, login


class SeleniumTest(unittest.TestCase):

    def setUp(self):
        self.driver = init_chromium('localhost')

    def tearDown(self):
        # pass
        self.driver.close()


class ChromiumTest(SeleniumTest):

    def test_init(self):
        self.assertIsNotNone(self.driver)
        print("type: %s" % type(self.driver))

    def test_login(self):
        login(self.driver)


if __name__ == '__main__':
    unittest.main()
