# linkedin/sessions/registry.py
from __future__ import annotations

import hashlib
import logging
from pathlib import Path  # noqa
from typing import Optional, NamedTuple

logger = logging.getLogger(__name__)


class AccountSessionRegistry:
    _instances: dict[SessionKey, "AccountSession"] = {}

    @classmethod
    def get_or_create(
            cls,
            handle: str,
            campaign_name: str,
            csv_hash: str,
    ) -> "AccountSession":
        from .account import AccountSession
        key = SessionKey(handle, campaign_name, csv_hash)

        if key not in cls._instances:
            cls._instances[key] = AccountSession(key)
            logger.info("Created new account session → %s", key)
        else:
            logger.debug("Reusing existing account session → %s", key)

        return cls._instances[key]

    @classmethod
    def get_or_create_from_path(
            cls,
            handle: str,
            campaign_name: str,
            csv_path: Path | str,
    ) -> "AccountSession":
        csv_path = Path(csv_path)
        key = SessionKey.make(handle, campaign_name, csv_path)
        return key, cls.get_or_create(key.handle, key.campaign_name, key.csv_hash)

    @classmethod
    def get_existing(cls, key: SessionKey) -> Optional["AccountSession"]:
        return cls._instances.get(key)

    @classmethod
    def clear_all(cls):
        for session in list(cls._instances.values()):
            session.close()
        cls._instances.clear()


class SessionKey(NamedTuple):
    handle: str
    campaign_name: str
    csv_hash: str

    def __str__(self) -> str:
        return f"{self.handle}::{self.campaign_name}::{self.csv_hash}"

    @classmethod
    def make(cls, handle: str, campaign_name: str, csv_path: Path | str) -> "SessionKey":
        csv_hash = hash_file(csv_path)
        return cls(handle=handle, campaign_name=campaign_name, csv_hash=csv_hash)

    def as_filename_safe(self) -> str:
        return f"{self.handle}--{self.campaign_name}--{self.csv_hash}"


def hash_file(path: Path | str, chunk_size: int = 8192, algorithm: str = "sha256") -> str:
    """
    Compute a stable cryptographic hash of a file's contents.
    Used to detect if the input CSV has changed → new campaign run.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Cannot hash file: {path} does not exist or is not a file")

    hasher = hashlib.new(algorithm)

    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)

    full_hex = hasher.hexdigest()
    short_hex = full_hex[:16]
    logger.debug(f"Hashed file {path.name} → {short_hex} (full: {full_hex})")
    return short_hex


# ——————————————————————————————————————————————————————————————
if __name__ == "__main__":
    import logging
    from pathlib import Path

    logging.getLogger().handlers.clear()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s │ %(levelname)-8s │ %(message)s",
        datefmt="%H:%M:%S",
    )

    import sys

    if len(sys.argv) != 2:
        print("Usage: python -m linkedin.sessions.registry <handle>")
        sys.exit(1)

    handle = sys.argv[1]

    CAMPAIGN_NAME = "connect_follow_up"
    INPUT_CSV_PATH = Path("./assets/inputs/urls.csv")

    _, session = AccountSessionRegistry.get_or_create_from_path(
        handle=handle,
        campaign_name=CAMPAIGN_NAME,
        csv_path=INPUT_CSV_PATH,
    )

    session.ensure_browser()  # ← this does everything

    print("\nSession ready! Use session.page, session.context, etc.")
    print(f"   Handle   : {session.handle}")
    print(f"   Campaign : {session.campaign_name}")
    print(f"   CSV hash : {session.csv_hash}")
    print(f"   Key      : {session.key}")
    print("   Browser survives crash/reboot/Ctrl+C\n")

    session.page.pause()  # keeps browser open for manual testing
