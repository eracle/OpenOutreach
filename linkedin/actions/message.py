# linkedin/actions/message.py
import logging
from typing import Dict, Any, Optional

from linkedin.actions.connection_status import get_connection_status
from linkedin.actions.search import search_profile
from linkedin.navigation.enums import ConnectionStatus, MessageStatus
from linkedin.navigation.utils import wait, PlaywrightResources
from linkedin.sessions import AccountSessionRegistry, SessionKey, AccountSession  # ← updated import
from linkedin.templates.renderer import render_template

logger = logging.getLogger(__name__)


def send_follow_up_message(
        key: SessionKey,  # ← changed
        profile: Dict[str, Any],
        *,
        template_file: Optional[str] = None,
        template_type: str = "jinja",
        message: Optional[str] = None,
):
    """Sends a follow-up message to a connected profile."""
    # ← only this block added/changed
    session = AccountSessionRegistry.get_or_create(
        handle=key.handle,
        campaign_name=key.campaign_name,
        csv_hash=key.csv_hash,
    )

    # ─────────────────────────────────────────────

    # Navigate to profile
    search_profile(session, profile)  # session instead of automation

    # Render message if not provided directly
    if message is None and template_file:
        message = render_template(template_file, template_type, profile)
    elif message is None:
        message = ""

    # Send
    status = send_message_to_profile(session, profile, message)
    logger.info(f"Message to {profile['url']} → {status.value}")


def get_messaging_availability(session: AccountSession, profile: Dict[str, Any]) -> bool:
    """
    Returns True if we are allowed to send a message.

    We allow messaging when:
      - The person is definitely CONNECTED (1st degree)
      - OR we don't know the status (UNKNOWN) → better to try than miss a real connection

    We block only when we're sure it's impossible (not connected or pending).
    """
    status = get_connection_status(session, profile)

    logger.debug("Checking messaging availability for %s → status: %s",
                 profile.get("full_name", "unknown"), status.value)

    # Allow messaging for connected people AND when status detection failed/unclear
    if status == ConnectionStatus.CONNECTED or status == ConnectionStatus.UNKNOWN:
        return True

    # Explicitly block only when we know messaging won't work
    if status == ConnectionStatus.NOT_CONNECTED:
        logger.info("Messaging blocked → not connected (2nd/3rd degree or out of network)")
    elif status == ConnectionStatus.PENDING:
        logger.info("Messaging blocked → connection request still pending")

    return False


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

    wait(resources, 3, 5)  # give LinkedIn time to load the message box

    input_area = resources.page.locator('div[class*="msg-form__contenteditable"]:visible').first

    # Method 1 – fill() – works 90% of the time in 2025
    try:
        input_area.fill(message, timeout=10000)
        print("Message typed with fill() → success")
    except:
        # Method 2 – clipboard paste (almost never fails)
        print("fill() failed → falling back to clipboard paste")
        input_area.click()
        resources.page.evaluate(f"navigator.clipboard.writeText(`{message.replace('`', '\\`')}`)")
        wait(resources)
        input_area.press("ControlOrMeta+V")
        wait(resources)

    wait(resources)

    send_btn = resources.page.locator('button[type="submit"][class*="msg-form"]:visible').first
    send_btn.click()
    wait(resources)


def send_message_to_profile(
        session: AccountSession,
        profile: Dict[str, Any],
        message: str,
) -> MessageStatus:
    resources = session.resources
    if not get_messaging_availability(session, profile):
        logger.info("Not connected → skipping message")
        return MessageStatus.SKIPPED

    _perform_send_message(resources, message)
    return MessageStatus.SENT


if __name__ == "__main__":
    import sys
    from linkedin.sessions import SessionKey
    from linkedin.campaigns.connect_follow_up import INPUT_CSV_PATH

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

    # ← only this part changed
    key = SessionKey.make(
        handle=handle,
        campaign_name="test_message",
        csv_path=INPUT_CSV_PATH,
    )

    # ← only this block added/changed
    session = AccountSessionRegistry.get_or_create(
        handle=key.handle,
        campaign_name=key.campaign_name,
        csv_hash=key.csv_hash,
    )

    profile = {
        "url": "https://www.linkedin.com/in/elizabethladendorf/",
        "public_identifier": "elizabethladendorf",
    }

    # Send
    send_follow_up_message(
        key=key,
        profile=profile,
        template_file="./assets/templates/messages/followup.j2",
        template_type="jinja",
    )
