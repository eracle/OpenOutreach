# campaigns/connect_follow_up.py
import logging

from termcolor import colored

from linkedin.actions.connection_status import get_connection_status
from linkedin.db.crm_profiles import set_profile_state, get_profile, save_scraped_profile
from linkedin.navigation.enums import MessageStatus
from linkedin.navigation.enums import ProfileState
from linkedin.navigation.exceptions import TerminalStateError, SkipProfile, ReachedConnectionLimit
from linkedin.navigation.utils import save_page

logger = logging.getLogger(__name__)

message_status_to_state = {
    MessageStatus.SENT: ProfileState.COMPLETED,
    MessageStatus.SKIPPED: ProfileState.CONNECTED,
}


def process_profile_row(
        handle: str,
        session: "AccountSession",
        simple_profile: dict,
        perform_connections=True,
):
    from linkedin.actions.connect import send_connection_request
    from linkedin.actions.message import send_follow_up_message
    from linkedin.actions.profile import scrape_profile

    url = simple_profile['url']
    public_identifier = simple_profile['public_identifier']
    profile_row = get_profile(session, public_identifier)

    if profile_row:
        current_state = ProfileState(profile_row.state)
        profile = profile_row.profile or simple_profile
    else:
        current_state = ProfileState.DISCOVERED
        profile = simple_profile

    logger.debug(f"Actual state: {public_identifier}  {current_state}")

    new_state = None
    match current_state:
        case ProfileState.COMPLETED | ProfileState.FAILED:
            return None

        case ProfileState.DISCOVERED:
            profile, data = scrape_profile(handle=handle, profile=profile)
            if profile is None:
                new_state = ProfileState.FAILED
            else:
                new_state = ProfileState.ENRICHED
                save_scraped_profile(session, url, profile, data)

        case ProfileState.ENRICHED:
            if not perform_connections:
                return None
            new_state = send_connection_request(handle=handle, profile=profile)
            profile = None if new_state != ProfileState.CONNECTED else profile
        case ProfileState.PENDING:
            new_state = get_connection_status(session, profile)
            profile = None if new_state != ProfileState.CONNECTED else profile
        case ProfileState.CONNECTED:
            status = send_follow_up_message(
                handle=handle,
                profile=profile,
            )
            new_state = message_status_to_state.get(status, ProfileState.CONNECTED)
            profile = None if status != MessageStatus.SENT else profile

        case _:
            raise TerminalStateError(f"Profile {public_identifier} is {current_state}")

    set_profile_state(session, public_identifier, new_state.value)

    return profile


def process_profiles(handle, session, profiles: list[dict]):
    perform_connections = True
    for simple_profile in profiles:
        continue_same_profile = True
        while continue_same_profile:
            try:
                profile = process_profile_row(
                    handle=handle,
                    session=session,
                    simple_profile=simple_profile,
                    perform_connections=perform_connections,
                )
                continue_same_profile = bool(profile)
            except SkipProfile as e:
                public_identifier = simple_profile["public_identifier"]
                logger.info(
                    colored(f"Skipping profile: {public_identifier} reason: {e}", "red", attrs=["bold"])
                )
                save_page(session, simple_profile)
                continue_same_profile = False
            except ReachedConnectionLimit as e:
                perform_connections = False
                public_identifier = simple_profile["public_identifier"]
                logger.info(
                    colored(f"Skipping profile: {public_identifier} reason: {e}", "red", attrs=["bold"])
                )
                continue_same_profile = False
