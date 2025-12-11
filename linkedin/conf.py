# linkedin/conf.py
import os
from pathlib import Path
from typing import Dict, Any, List

import yaml
from dotenv import load_dotenv

load_dotenv()

# ----------------------------------------------------------------------
# Global OpenAI config
# ----------------------------------------------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
AI_MODEL = os.getenv("AI_MODEL", "gpt-4o-mini")

# ----------------------------------------------------------------------
# Paths (all under assets/)
# ----------------------------------------------------------------------
ROOT_DIR = Path(__file__).parent.parent
ASSETS_DIR = ROOT_DIR / "assets"

COOKIES_DIR = ASSETS_DIR / "cookies"
DATA_DIR = ASSETS_DIR / "data"

COOKIES_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

FIXTURE_DIR = ROOT_DIR / "tests" / "fixtures"
FIXTURE_PROFILES_DIR = FIXTURE_DIR / "profiles"
FIXTURE_PAGES_DIR = FIXTURE_DIR / "pages"

# ←←← FEATURE FLAG – set to False to completely disable auto-scraping in wait() ←←←
SYNC_PROFILES = False

# ----------------------------------------------------------------------
# SINGLE secrets file
# ----------------------------------------------------------------------
SECRETS_PATH = ASSETS_DIR / "accounts.secrets.yaml"

if not SECRETS_PATH.exists():
    raise FileNotFoundError(
        f"\nMissing config file: {SECRETS_PATH}\n"
        "→ cp assets/accounts.secrets.example.yaml assets/accounts.secrets.yaml\n"
        "  and fill in your accounts (public settings + credentials)\n"
    )

# Load everything from the single secrets file
with open(SECRETS_PATH, "r", encoding="utf-8") as f:
    _raw_config = yaml.safe_load(f) or {}

_accounts_config = _raw_config.get("accounts", {})


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------
def get_account_config(handle: str) -> Dict[str, Any]:
    """Return full config (public + secrets) for a handle from the single file."""
    if handle not in _accounts_config:
        raise KeyError(f"Account '{handle}' not found in {SECRETS_PATH}")

    acct = _accounts_config[handle]

    # Each account gets its own database: assets/data/elonmusk.db
    account_db_path = DATA_DIR / f"{handle}.db"

    return {
        "handle": handle,
        "active": acct.get("active", True),
        "proxy": acct.get("proxy"),
        "daily_connections": acct.get("daily_connections", 50),
        "daily_messages": acct.get("daily_messages", 20),
        # Credentials (can be missing during dev, but required in prod)
        "username": acct.get("username"),
        "password": acct.get("password"),
        # Runtime paths
        "cookie_file": COOKIES_DIR / f"{handle}.json",
        "db_path": account_db_path,  # per-handle database
    }


def list_active_accounts() -> List[str]:
    """Return list of active account handles (order preserved from YAML)."""
    return [
        handle for handle, cfg in _accounts_config.items()
        if cfg.get("active", True)
    ]


def get_first_active_account() -> str | None:
    """
    Return the first active account handle from the config, or None if no active accounts.

    The order is deterministic: it follows the insertion order in accounts.secrets.yaml
    (YAML dictionaries preserve order since Python 3.7+).
    """
    active = list_active_accounts()
    return active[0] if active else None


def get_first_account_config() -> Dict[str, Any] | None:
    """
    Return the complete config dict for the first active account, or None if none exist.
    """
    handle = get_first_active_account()
    if handle is None:
        return None
    return get_account_config(handle)


# ----------------------------------------------------------------------
# Debug output when run directly
# ----------------------------------------------------------------------
if __name__ == "__main__":
    print("LinkedIn Automation – Active accounts")
    print(f"Config file : {SECRETS_PATH}")
    print(f"Databases stored in: {DATA_DIR}")
    print("-" * 60)

    active_handles = list_active_accounts()
    if not active_handles:
        print("No active accounts found.")
    else:
        for handle in active_handles:
            cfg = get_account_config(handle)
            status = "ACTIVE" if cfg["active"] else "inactive"
            print(f"{status} • {handle.ljust(20)}  →  DB: {cfg['db_path'].name}")

        print("-" * 60)
        first = get_first_active_account()
        print(f"First active account → {first or 'None'}")
