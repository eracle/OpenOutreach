# linkedin/actions/connect.py
import logging
from enum import Enum
from typing import Dict, Any

from linkedin.actions.login import PlaywrightResources, get_resources_with_state_management
from linkedin.actions.navigation import go_to_profile
from linkedin.actions.utils import wait
from ..template_renderer import render_template

logger = logging.getLogger(__name__)


class ConnectionStatus(Enum):
    CONNECTED = "connected"
    PENDING = "pending"
    NOT_CONNECTED = "not_connected"
    UNKNOWN = "unknown"


def connect(context: Dict[str, Any], profile: Dict[str, Any]):
    """Sends a connection request to a profile."""

    resources = context['resources']

    # Navigate to the profile
    go_to_profile(resources, profile)

    # Render the message if a template is provided
    message = render_template(context['params'].get('note_template'),
                              context['params'].get('template_type', 'jinja'),
                              profile
                              )

    # Send the connection request
    status = send_connection_request(resources, profile, message)
    logger.info(f"Connection request for {profile['linkedin_url']} completed with status: {status.value}")


def get_connection_status(
        resources: PlaywrightResources,
        profile: Dict[str, Any],
) -> ConnectionStatus:
    """Checks the connection status of a LinkedIn profile."""
    # 1. Pending – very reliable, text is almost always in English ("Pending") even on localized UIs
    if resources.page.locator('button[aria-label*="Pending"]:visible').count() > 0:
        return ConnectionStatus.PENDING

    # 2. Already connected – distance badge is present and starts with "1"
    #     Works for English ("1st"), Italian/Spanish/Portuguese ("1°"), French ("1er"), etc.
    dist_locator = resources.page.locator('span[class^="distance-badge"]:visible')
    if dist_locator.count() > 0:
        badge_text = dist_locator.first.inner_text().strip()
        if badge_text.startswith("1"):
            return ConnectionStatus.CONNECTED

    # 3. Can send a request – direct "Invite … to connect" button exists
    if resources.page.locator('button[aria-label*="Invite"]:visible').count() > 0:
        return ConnectionStatus.NOT_CONNECTED

    # 4. Fallback
    return ConnectionStatus.UNKNOWN


def _perform_send_invitation(
        resources: PlaywrightResources,
        message: str,
):
    """Performs the actual steps to send the invitation after status check."""

    # Wait for the dropdown to load
    wait(resources)

    # Try direct connect button first
    direct_connect_locator = 'button[aria-label*="Invite"]:visible'
    direct_connect = resources.page.locator(direct_connect_locator)
    if direct_connect.count() > 0:
        direct_connect.first.click()
    else:
        # Click the 'More' button
        more_button = resources.page.locator('button[id$="profile-overflow-action"]:visible').first
        more_button.click()

        # Wait for the dropdown to load
        wait(resources)  # Random sleep

        # Locate and click the connect div
        connect_div_locator = 'div[aria-label$="to connect"]:visible'
        connect_div = resources.page.locator(connect_div_locator).first
        connect_div.click()

    # After initiating connect, wait for popup
    wait(resources)  # Random sleep

    if not message:
        # Send button for without note
        send_button_locator = 'button[aria-label*="Send"]:visible'
    else:
        # Locate and click the "Add a note" button
        add_a_note_locator = 'button[aria-label*="Add"]:visible'
        resources.page.locator(add_a_note_locator).first.click()

        # Wait for the input to appear
        wait(resources)  # Random sleep

        # Locate the textarea and type the message
        message_locator = 'textarea[name*="message"]:visible'
        resources.page.locator(message_locator).first.type(message, delay=150)

        # Wait for stability
        wait(resources)  # Random sleep

        # Send button for with note
        send_button_locator = 'button[aria-label*="Send invitation"]:visible'

    # Locate and click the send button
    resources.page.locator(send_button_locator).first.click()
    wait(resources)  # Random sleep after send


def send_connection_request(
        resources: PlaywrightResources,
        profile: Dict[str, Any],
        message=None,
) -> ConnectionStatus:
    """Navigates to a LinkedIn profile, checks status, and sends a connection request if not connected or pending."""
    # Get current status
    status = get_connection_status(resources, profile)

    skip_statuses = {
        ConnectionStatus.CONNECTED: ("info", "Already connected. Skipping send."),
        ConnectionStatus.PENDING: ("info", "Connection request is already pending. Skipping send."),
        ConnectionStatus.UNKNOWN: ("warning", "Unknown connection status. Skipping send.")
    }

    if status in skip_statuses:
        log_level, msg = skip_statuses[status]
        logger_func = logger.warning if log_level == "warning" else logger.info
        logger_func(msg)
        return status

    # If not connected, proceed to send
    _perform_send_invitation(resources, message)

    # Assume success, return pending
    return ConnectionStatus.PENDING


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

    # Example params from YAML (adjust as needed for testing)
    params = {
        "note_template": "./assets/templates/connect_notes/leader.j2",  # Replace with actual path
        "template_type": "jinja",  # Test with 'static', 'jinja', or 'ai_prompt'
    }

    # Construct context
    context = {
        'resources': get_resources_with_state_management(use_state=True, force_login=False),
        'params': params
    }

    connect(context, target_profile)
