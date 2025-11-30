# linkedin/conf.py
import os
from pathlib import Path
from typing import Dict, Any

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
DATA_DIR = ASSETS_DIR / "data"  # This will now contain one .db per account

COOKIES_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

# ----------------------------------------------------------------------
# SINGLE secrets file (unchanged)
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
        "display_name": acct.get("display_name", handle.replace("_", " ").title()),
        "proxy": acct.get("proxy"),
        "daily_connections": acct.get("daily_connections", 50),
        "daily_messages": acct.get("daily_messages", 20),
        # Credentials (can be missing during dev, but required in prod)
        "username": acct.get("username"),
        "password": acct.get("password"),
        # Runtime paths
        "cookie_file": COOKIES_DIR / f"{handle}.json",
        "db_path": account_db_path,  # ← this is now per-handle
    }


def list_active_accounts() -> list[str]:
    """Return list of active account handles"""
    return [
        handle for handle, cfg in _accounts_config.items()
        if cfg.get("active", True)
    ]


# ----------------------------------------------------------------------
# Debug output when run directly
# ----------------------------------------------------------------------
if __name__ == "__main__":
    print("LinkedIn Automation – Active accounts")
    print(f"Config file : {SECRETS_PATH}")
    print(f"Databases stored in: {DATA_DIR}")
    print("-" * 60)
    for handle in list_active_accounts():
        cfg = get_account_config(handle)
        status = "ACTIVE" if cfg["active"] else "inactive"
        print(f"{status} • {handle.ljust(20)}  →  {cfg['display_name']}")
        print(f"               DB: {cfg['db_path'].name}")
