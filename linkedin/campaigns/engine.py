# campaigns/engine.py
from __future__ import annotations

import logging

from linkedin.api.emails import ensure_newsletter_subscription
from linkedin.campaigns.connect_follow_up import process_profiles
from linkedin.sessions.account import AccountSession
from linkedin.sessions.registry import SessionKey

logger = logging.getLogger(__name__)


def start_campaign(key: SessionKey, session: AccountSession, profiles: list[dict]):
    session.ensure_browser()

    ensure_newsletter_subscription(session)

    process_profiles(key, session, profiles)
