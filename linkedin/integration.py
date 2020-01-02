from time import sleep

import random
from linkedin_api import Linkedin


def my_default_evade():
    """
    A catch-all method to try and evade suspension from Linkedin.
    Currenly, just delays the request by a random (bounded) time
    """
    sleep(random.uniform(0.2, 0.7))  # sleep a random duration to try and evade suspention


class CustomLinkedinClient(Linkedin):

    def _fetch(self, uri, evade=my_default_evade, **kwargs):
        """
        GET request to Linkedin API
        """
        return super()._fetch(uri, evade, **kwargs)

    def _post(self, uri, evade=my_default_evade, **kwargs):
        """
        POST request to Linkedin API
        """
        return super()._post(uri, evade, **kwargs)
