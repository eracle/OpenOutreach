# linkedin/actions/message.py
import logging
from typing import Dict, Any, Optional

from linkedin.actions.connections import get_connection_status
from linkedin.actions.search import search_profile
from linkedin.navigation.enums import ConnectionStatus, MessageStatus
from linkedin.navigation.utils import wait, PlaywrightResources
from linkedin.templates.renderer import render_template

logger = logging.getLogger(__name__)


def send_follow_up_message(
        automation: "LinkedInAutomation",
        profile: Dict[str, Any],
        *,
        template_file: Optional[str] = None,
        template_type: str = "jinja",
        message: Optional[str] = None,
):
    """Sends a follow-up message to a connected profile."""
    resources = automation.browser

    # Navigate to profile
    search_profile(automation, profile)

    # Render message if not provided directly
    if message is None and template_file:
        message = render_template(template_file, template_type, profile)
    elif message is None:
        message = ""

    # Send
    status = send_message_to_profile(resources, profile, message)
    logger.info(f"Message to {profile['linkedin_url']} → {status.value}")


def get_messaging_availability(resources: PlaywrightResources, profile: Dict[str, Any]) -> bool:
    return get_connection_status(resources, profile) == ConnectionStatus.CONNECTED


def _perform_send_message(resources: PlaywrightResources, message: str):
    wait(resources)

    direct = resources.page.locator('button[aria-label*="Message"]:visible')
    if direct.count() > 0:
        direct.first.click()
    else:
        more = resources.page.locator('button[id$="profile-overflow-action"]:visible').first
        more.click()
        wait(resources)
        msg_option = resources.page.locator('div[aria-label$="to message"]:visible').first
        msg_option.click()

    wait(resources)

    input_area = resources.page.locator('div[class*="msg-form__contenteditable"]:visible').first
    input_area.type(message, delay=150)
    wait(resources)

    send_btn = resources.page.locator('button[type="submit"][class*="msg-form"]:visible').first
    send_btn.click()
    wait(resources)


def send_message_to_profile(
        resources: PlaywrightResources,
        profile: Dict[str, Any],
        message: str,
) -> MessageStatus:
    if not get_messaging_availability(resources, profile):
        logger.info("Not connected → skipping message")
        return MessageStatus.SKIPPED

    try:
        _perform_send_message(resources, message)
        return MessageStatus.SENT
    except Exception as e:
        logger.warning(f"Failed to send message: {e}")
        return MessageStatus.UNKNOWN


if __name__ == "__main__":
    import sys
    from pathlib import Path
    from linkedin.automation import AutomationRegistry

    root_logger = logging.getLogger()
    root_logger.handlers = []
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    if len(sys.argv) != 2:
        print("Usage: python -m linkedin.actions.message <handle>")
        sys.exit(1)

    handle = sys.argv[1]

    automation = AutomationRegistry.get_or_create(
        handle=handle,
        campaign_name="test_message",
        csv_hash="debug",
        input_csv=Path("dummy.csv"),

    )

    target_profile = {
        "full_name": "Bill Gates",
        "linkedin_url": "https://www.linkedin.com/in/williamhgates/",
        "public_id": "williamhgates",
    }

    try:
        send_follow_up_message(
            automation=automation,
            profile=target_profile,
            template_file="./assets/templates/prompts/followup.j2",
            template_type="static",
        )
    except Exception as e:
        logger.exception("Message test failed")
        raise
