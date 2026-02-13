# linkedin/sessions/registry.py
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class AccountSessionRegistry:
    """
    Singleton-like registry where each LinkedIn handle has exactly ONE AccountSession.
    Ignores campaign_name and csv_hash — only handle matters.
    """
    _instances: dict[str, "AccountSession"] = {}

    @classmethod
    def get_or_create(cls, handle: str) -> "AccountSession":
        """
        Main method - get or create session for given handle.
        Handle is normalized (lowercase + strip) to avoid duplicates.
        """
        from .account import AccountSession

        normalized = cls._normalize_handle(handle)

        if normalized not in cls._instances:
            session = AccountSession(handle=normalized)  # ← pass only handle
            cls._instances[normalized] = session
            logger.debug("Created new account session for handle → %s", normalized)
        else:
            logger.debug("Reusing existing account session for handle → %s", normalized)

        return cls._instances[normalized]

    @classmethod
    def get(cls, handle: str) -> Optional["AccountSession"]:
        """Just get existing session or None"""
        normalized = cls._normalize_handle(handle)
        return cls._instances.get(normalized)

    @classmethod
    def exists(cls, handle: str) -> bool:
        return cls._normalize_handle(handle) in cls._instances

    @classmethod
    def close_all(cls):
        """Close all open sessions (useful on application shutdown)"""
        for handle, session in list(cls._instances.items()):
            try:
                session.close()
                logger.info("Closed session for handle → %s", handle)
            except Exception as e:
                logger.warning("Error while closing session %s: %s", handle, e)
        cls._instances.clear()

    @staticmethod
    def _normalize_handle(handle: str) -> str:
        """Standardize handle format → case insensitive & clean"""
        if not handle:
            raise ValueError("Handle cannot be empty")
        return handle.strip().lower()


def get_session(handle: str) -> "AccountSession":
    return AccountSessionRegistry.get_or_create(handle)


# For convenience in scripts/tests
if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s │ %(levelname)-8s │ %(message)s",
        datefmt="%H:%M:%S",
    )

    if len(sys.argv) != 2:
        print("Usage: python -m linkedin.sessions.registry <handle>")
        sys.exit(1)

    handle = sys.argv[1]

    session = get_session(handle)

    print("\nSession ready!")
    print(f"   Handle   : {session.handle}")
    print("   → Same handle = same session instance (always)")

    try:
        session.ensure_browser()
        session.page.pause()  # keep browser open for manual testing
    except KeyboardInterrupt:
        print("\nClosing session...")
        session.close()
