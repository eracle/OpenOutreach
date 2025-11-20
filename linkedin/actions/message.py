# linkedin/actions/message.py
import logging
from enum import Enum
from typing import Dict, Any, Optional

from linkedin.actions.login import PlaywrightResources, get_resources_with_state_management
from linkedin.actions.navigation import go_to_profile
from linkedin.actions.utils import wait
from ..actions.connect import get_connection_status, ConnectionStatus

logger = logging.getLogger(__name__)


class MessageStatus(Enum):
    SENT = "sent"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"


def send_message(context: Dict[str, Any], profile_data: dict):
    """Sends a message to a profile."""

    resources = context['resources']

    # Navigate to the profile
    go_to_profile(resources, profile_data)

    # Hardcoded message for now
    message = "Hello, this is a test message."

    # Send the message
    status = send_message_to_profile(resources, profile_data, message)
    logger.info(f"Message for {profile_data['linkedin_url']} completed with status: {status.value}")


def get_messaging_availability(
        resources: PlaywrightResources,
        profile: Dict[str, Any],
) -> bool:
    """Checks if messaging is available (i.e., connected)."""
    connection_status = get_connection_status(resources, profile)
    return connection_status == ConnectionStatus.CONNECTED


def _perform_send_message(
        resources: PlaywrightResources,
        message: str,
):
    """Performs the actual steps to send the message after availability check."""

    # Wait for the page to stabilize
    wait(resources)

    # Try direct message button first
    direct_message_locator = 'button[aria-label*="Message"]:visible'
    direct_message = resources.page.locator(direct_message_locator)
    if direct_message.count() > 0:
        direct_message.first.click()
    else:
        # Click the 'More' button if direct not found
        more_button = resources.page.locator('button[id$="profile-overflow-action"]:visible').first
        more_button.click()

        # Wait for the dropdown to load
        wait(resources)

        # Locate and click the message div
        message_div_locator = 'div[aria-label$="to message"]:visible'  # Adjusted based on typical LinkedIn labels
        message_div = resources.page.locator(message_div_locator).first
        message_div.click()

    # After initiating message, wait for messaging interface
    wait(resources)

    # Locate the contenteditable div for message input (LinkedIn uses contenteditable for messaging)
    message_input_locator = 'div[class*="msg-form__contenteditable"]:visible'
    resources.page.locator(message_input_locator).first.type(message, delay=150)

    # Wait for stability
    wait(resources)

    # Locate and click the send button (targeting type="submit" for form submission)
    send_button_locator = 'button[type="submit"][class*="msg-form"]:visible'
    resources.page.locator(send_button_locator).first.click()
    wait(resources)  # Random sleep after send


def send_message_to_profile(
        resources: PlaywrightResources,
        profile: Dict[str, Any],
        message: str,
) -> MessageStatus:
    """Checks availability and sends a message if possible."""
    if not get_messaging_availability(resources, profile):
        logger.info("Not connected or unable to message. Skipping send.")
        return MessageStatus.SKIPPED

    # If available, proceed to send
    try:
        _perform_send_message(resources, message)
        return MessageStatus.SENT
    except Exception as e:
        logger.warning(f"Failed to send message: {e}")
        return MessageStatus.UNKNOWN


if __name__ == "__main__":
    # Forcefully reset and configure logging to ensure reliability
    root_logger = logging.getLogger()
    root_logger.handlers = []  # Clear any existing handlers to avoid conflicts
    logging.basicConfig(
        level=logging.DEBUG,  # Set to DEBUG for more logs; change to INFO if too verbose
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Example profile data for testing
    target_profile = {
        "full_name": "Bill Gates",
        "linkedin_url": "https://www.linkedin.com/in/williamhgates/",
        "public_id": "williamhgates",
    }
    context = dict(
        resources=get_resources_with_state_management(use_state=True, force_login=False)
    )

    # Or without context
    send_message(context, target_profile)
