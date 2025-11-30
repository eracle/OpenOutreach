import logging
import sys

from linkedin.csv_launcher import launch_connect_follow_up_campaign

logging.getLogger().handlers.clear()
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s │ %(levelname)-8s │ %(message)s",
    datefmt="%H:%M:%S",
)

if __name__ == "__main__":

    if len(sys.argv) != 2:
        print("Usage: python -m linkedin.actions.connect <handle>")
        print("Example: python -m linkedin.actions.connect john_doe_2025")
        sys.exit(1)

    handle = sys.argv[1]
    launch_connect_follow_up_campaign(handle)
