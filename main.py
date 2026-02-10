import logging
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "linkedin.django_settings")

import django
django.setup()

from linkedin.csv_launcher import launch_connect_follow_up_campaign

logging.getLogger().handlers.clear()
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)

if __name__ == "__main__":
    handle = sys.argv[1] if len(sys.argv) > 1 else None
    launch_connect_follow_up_campaign(handle)
