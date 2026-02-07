import logging
import sys

from linkedin.csv_launcher import launch_connect_follow_up_campaign

logging.getLogger().handlers.clear()
logging.basicConfig(
    level=logging.DEBUG,
    format="%(message)s",
)

if __name__ == "__main__":
    handle = sys.argv[1] if len(sys.argv) > 1 else None
    launch_connect_follow_up_campaign(handle)
