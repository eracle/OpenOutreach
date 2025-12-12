# campaigns/connect_follow_up.py
import logging
from pathlib import Path

from linkedin.db.profiles import set_profile_state, get_profile, save_scraped_profile
from linkedin.navigation.enums import MessageStatus
from linkedin.navigation.exceptions import TerminalStateError
from linkedin.sessions.registry import SessionKey

logger = logging.getLogger(__name__)

# ———————————————————————————————— USER CONFIGURATION ————————————————————————————————
CAMPAIGN_NAME = "connect_follow_up"
INPUT_CSV_PATH = Path("./assets/inputs/urls.csv")

# ———————————————————————————————— Template Config ————————————————————————————————
# CONNECT_TEMPLATE_FILE = "./assets/templates/connect_notes/leader.j2"
# CONNECT_TEMPLATE_TYPE = "jinja"

FOLLOWUP_TEMPLATE_FILE = "./assets/templates/prompts/followup.j2"
FOLLOWUP_TEMPLATE_TYPE = "ai_prompt"


# ———————————————————————————————— Core Logic ————————————————————————————————
def process_profile_row(
        key: SessionKey,
        session: "AccountSession",
        profile: dict,
):
    from linkedin.actions.connect import send_connection_request
    from linkedin.actions.message import send_follow_up_message
    from linkedin.actions.profile import scrape_profile
    from linkedin.navigation.enums import ConnectionStatus, ProfileState  # ← added ProfileState

    url = profile['url']
    public_identifier = profile['public_identifier']
    profile_row = get_profile(session, public_identifier)

    if profile_row:
        current_state = ProfileState(profile_row.state)  # ← string → enum
        enriched_profile = profile_row.profile or profile
    else:
        current_state = ProfileState.DISCOVERED
        enriched_profile = profile

    logger.debug(f"Actual state: {public_identifier}  {current_state}")

    new_state = None
    match current_state:
        case ProfileState.COMPLETED | ProfileState.FAILED:
            return None

        case ProfileState.DISCOVERED:
            enriched_profile, data = scrape_profile(key=key, profile=enriched_profile)
            if enriched_profile is None:
                new_state = ProfileState.FAILED
            else:
                new_state = ProfileState.ENRICHED
                save_scraped_profile(session, url, enriched_profile, data)

        case ProfileState.ENRICHED:
            status = send_connection_request(key=key, profile=enriched_profile)
            if status != ConnectionStatus.CONNECTED:
                return False
            new_state = ProfileState.CONNECTED

        case ProfileState.CONNECTED:
            status = send_follow_up_message(
                key=key,
                profile=enriched_profile,
                template_file=FOLLOWUP_TEMPLATE_FILE,
                template_type=FOLLOWUP_TEMPLATE_TYPE,
            )
            if status != MessageStatus.SENT:
                return False
            new_state = ProfileState.COMPLETED

        case _:
            raise TerminalStateError(f"Profile {public_identifier} is {current_state}")

    set_profile_state(session, public_identifier, new_state.value)

    return enriched_profile
