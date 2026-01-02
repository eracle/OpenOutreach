# campaigns/connect_follow_up.py
import logging
from pathlib import Path

from termcolor import colored

from linkedin.actions.connection_status import get_connection_status
from linkedin.db.profiles import set_profile_state, get_profile, save_scraped_profile
from linkedin.navigation.enums import MessageStatus
from linkedin.navigation.enums import ProfileState
from linkedin.navigation.exceptions import TerminalStateError, SkipProfile, ReachedConnectionLimit
from linkedin.navigation.utils import save_page
from linkedin.sessions.registry import SessionKey

logger = logging.getLogger(__name__)

# ———————————————————————————————— USER CONFIGURATION ————————————————————————————————
CAMPAIGN_NAME = "connect_follow_up"
INPUT_CSV_PATH = Path("./assets/inputs/urls.csv")

# ———————————————————————————————— Template Config ————————————————————————————————

FOLLOWUP_TEMPLATE_FILE = "./assets/templates/prompts/followup.j2"
FOLLOWUP_TEMPLATE_TYPE = "ai_prompt"

message_status_to_state = {
    MessageStatus.SENT: ProfileState.COMPLETED,
    MessageStatus.SKIPPED: ProfileState.CONNECTED,
}


# ———————————————————————————————— Core Logic ————————————————————————————————
def process_profile_row(
        key: SessionKey,
        session: "AccountSession",
        profile: dict,
        perform_connections=True,
):
    from linkedin.actions.connect import send_connection_request
    from linkedin.actions.message import send_follow_up_message
    from linkedin.actions.profile import scrape_profile
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
            if not perform_connections:
                return None
            new_state = send_connection_request(key=key, profile=enriched_profile)
            enriched_profile = None if new_state != ProfileState.CONNECTED else enriched_profile
        case ProfileState.PENDING:
            new_state = get_connection_status(session, profile)
            enriched_profile = None if new_state != ProfileState.CONNECTED else enriched_profile
        case ProfileState.CONNECTED:
            status = send_follow_up_message(
                key=key,
                profile=enriched_profile,
                template_file=FOLLOWUP_TEMPLATE_FILE,
                template_type=FOLLOWUP_TEMPLATE_TYPE,
            )
            new_state = message_status_to_state.get(status, ProfileState.CONNECTED)
            enriched_profile = None if status != MessageStatus.SENT else enriched_profile

        case _:
            raise TerminalStateError(f"Profile {public_identifier} is {current_state}")

    set_profile_state(session, public_identifier, new_state.value)

    return enriched_profile


def process_profiles(key, session, profiles: list[dict]):
    perform_connections = True
    for profile in profiles:
        continue_same_profile = True
        while continue_same_profile:
            try:
                profile = process_profile_row(
                    key=key,
                    session=session,
                    profile=profile,
                    perform_connections=perform_connections,
                )
                continue_same_profile = bool(profile)
            except SkipProfile as e:
                public_identifier = profile["public_identifier"]
                logger.info(
                    colored(f"Skipping profile: {public_identifier} reason: {e}", "red", attrs=["bold"])
                )
                save_page(session, profile)
                continue_same_profile = False
            except ReachedConnectionLimit as e:
                perform_connections = False
                public_identifier = profile["public_identifier"]
                logger.info(
                    colored(f"Skipping profile: {public_identifier} reason: {e}", "red", attrs=["bold"])
                )
                continue_same_profile = False
