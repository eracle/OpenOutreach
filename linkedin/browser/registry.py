# linkedin/browser/registry.py
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class AccountSessionRegistry:
    """Singleton registry: each LinkedInProfile has exactly ONE AccountSession."""
    _instances: dict[int, "AccountSession"] = {}

    @classmethod
    def get_or_create(cls, linkedin_profile) -> "AccountSession":
        from linkedin.browser.session import AccountSession

        pk = linkedin_profile.pk
        if pk not in cls._instances:
            session = AccountSession(linkedin_profile)
            cls._instances[pk] = session
            logger.debug("Created new account session for %s", linkedin_profile)
        else:
            logger.debug("Reusing existing account session for %s", linkedin_profile)

        return cls._instances[pk]

    @classmethod
    def get(cls, linkedin_profile) -> Optional["AccountSession"]:
        return cls._instances.get(linkedin_profile.pk)

    @classmethod
    def exists(cls, linkedin_profile) -> bool:
        return linkedin_profile.pk in cls._instances

    @classmethod
    def close_all(cls):
        for pk, session in list(cls._instances.items()):
            try:
                session.close()
                logger.info("Closed session for %s", session)
            except Exception as e:
                logger.warning("Error closing session %s: %s", session, e)
        cls._instances.clear()


def get_or_create_session(linkedin_profile) -> "AccountSession":
    return AccountSessionRegistry.get_or_create(linkedin_profile)
