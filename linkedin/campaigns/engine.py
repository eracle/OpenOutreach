from linkedin.campaigns.connect_follow_up import process_profiles


def start_campaign(key, session, profiles: list[dict]):
    session.ensure_browser()
    process_profiles(key, session, profiles)
