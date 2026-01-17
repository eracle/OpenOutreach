# campaigns/engine.py
from __future__ import annotations

import logging

from linkedin.api.emails import ensure_newsletter_subscription
from linkedin.campaigns.connect_follow_up import process_profiles
from linkedin.sessions.account import AccountSession

logger = logging.getLogger(__name__)


def start_campaign(handle: str, session: AccountSession, profiles: list[dict]):
    session.ensure_browser()

    ensure_newsletter_subscription(session)

    process_profiles(handle, session, profiles)
