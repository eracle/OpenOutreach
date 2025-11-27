# linkedin/actions/connect.py
import logging
from typing import Optional, Dict, Any


from linkedin.navigation.enums import ConnectionStatus
from linkedin.navigation.utils import wait
from linkedin.templates.renderer import render_template

logger = logging.getLogger(__name__)


def send_connection_request(
        automation,
        profile: Dict[str, Any],
        template_file: Optional[str] = None,
        template_type: str = "jinja",
) -> ConnectionStatus:
    """
    High-level action: sends a connection request using the current automation context.

    This is the function you call from your workflow:
        automation.send_connection_request(profile, template_file="...")

    Args:
        automation: LinkedInAutomation instance (provides browser + state)
        profile: Profile dict with at least 'linkedin_url'
        template_file: Path to .j2 / .txt template
        template_type: 'jinja' | 'static' | 'ai_prompt'
        message: Pre-rendered message (overrides template)

    Returns:
        Final ConnectionStatus after attempt
    """
    from linkedin.actions.search import search_profile
    from linkedin.actions.connections import get_connection_status

    resources = automation.browser
    page = resources.page

    # 1. Navigate to profile
    search_profile(automation, profile)

    # 2. Render message if needed
    if template_file:
        message = render_template(template_file, template_type, profile)
    else:
        message = ""

    # 3. Check current status
    status = get_connection_status(resources, profile)

    skip_reasons = {
        ConnectionStatus.CONNECTED: "Already connected",
        ConnectionStatus.PENDING: "Invitation already pending",
        ConnectionStatus.UNKNOWN: "Unknown status – playing safe",
    }

    if status in skip_reasons:
        logger.info(f"Skipping {profile.get('full_name', profile['linkedin_url'])} → {skip_reasons[status]}")
        return status

    # 4. Send the invitation
    try:
        _perform_send_invitation(resources, message.strip())
        logger.info(f"Connection request sent → {profile.get('full_name') or profile['linkedin_url']}")
        return ConnectionStatus.PENDING
    except Exception as e:
        logger.error(f"Failed to send invite to {profile['linkedin_url']}: {e}")
        return ConnectionStatus.UNKNOWN


def _perform_send_invitation(resources, message: str):
    """Low-level click logic – isolated for easier maintenance."""
    wait(resources)

    # Primary: Direct "Connect" button
    direct = resources.page.locator('button[aria-label*="Invite"][aria-label*="to connect"]:visible')
    if direct.count() > 0:
        direct.first.click()
        logger.debug("Used direct Connect button")
    else:
        # Fallback: More → Connect
        more = resources.page.locator(
            'button[id*="overflow"]:visible, button[aria-label*="More actions"]:visible').first
        more.click()
        wait(resources)
        connect_option = resources.page.locator(
            'div[role="menuitem"]:has-text("Connect"), span:has-text("Connect")').first
        connect_option.click()
        logger.debug("Used More → Connect")

    wait(resources)

    if not message:
        # Send without note
        send_btn = resources.page.locator('button:has-text("Send now"), button[aria-label*="Send without"]')
    else:
        # Add note flow
        add_note = resources.page.locator('button:has-text("Add a note"), button[aria-label*="Add a note"]')
        add_note.first.click()
        wait(resources)

        textarea = resources.page.locator('textarea#custom-message, textarea[name="message"]')
        textarea.first.fill(message)
        wait(resources)

        send_btn = resources.page.locator('button:has-text("Send"), button[aria-label*="Send invitation"]')

    send_btn.first.click(force=True)
    wait(resources, min_wait=1.5)  # Slightly longer after send
    logger.debug("Invite modal submitted")


# ===================================================================
# Minimal __main__ – only requires <handle>
# ===================================================================
if __name__ == "__main__":
    import sys
    from pathlib import Path
    from linkedin.automation import AutomationRegistry

    if len(sys.argv) != 2:
        print("Usage: python -m linkedin.actions.connect <handle>")
        print("Example: python -m linkedin.actions.connect john_doe_2025")
        sys.exit(1)

    handle = sys.argv[1]

    # Fixed values – same as your original test
    campaign_name = "test_connect"
    csv_hash = "debug"
    input_csv = Path("debug_input.csv")  # dummy – not actually read
    template_file = "./assets/templates/connect_notes/leader.j2"

    # Create singleton (will auto-recover browser session)
    automation = AutomationRegistry.get_or_create(
        handle=handle,
        campaign_name=campaign_name,
        csv_hash=csv_hash,
        input_csv=input_csv,
    )

    # Basic logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # Hardcoded test profile (Bill Gates – safe, public, never changes)
    test_profile = {
        "full_name": "Bill Gates",
        "linkedin_url": "https://www.linkedin.com/in/williamhgates/",
        "public_id": "williamhgates",
    }

    print(f"Testing connection request as @{handle} → {test_profile['full_name']}")
    status = send_connection_request(
        automation=automation,
        profile=test_profile,
        template_file=template_file,
        template_type="jinja",
    )

    print(f"Finished → Status: {status.value}")
