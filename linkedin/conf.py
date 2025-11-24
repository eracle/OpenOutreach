# linkedin/conf.py
import os
import yaml
from pathlib import Path
from typing import Dict, Any

from dotenv import load_dotenv
load_dotenv()  # Optional .env support (kept for OPENAI_API_KEY)

# ----------------------------------------------------------------------
# Global OpenAI config (unchanged from your original)
# ----------------------------------------------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
AI_MODEL = os.getenv("AI_MODEL", "gpt-4o-mini")

# ----------------------------------------------------------------------
# All paths are under assets/
# ----------------------------------------------------------------------
ROOT_DIR = Path(__file__).parent.parent
ASSETS_DIR = ROOT_DIR / "assets"

COOKIES_DIR = ASSETS_DIR / "cookies"
DATA_DIR = ASSETS_DIR / "data"

# Create runtime directories
COOKIES_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

# ----------------------------------------------------------------------
# Load accounts config
# ----------------------------------------------------------------------
_accounts_path = ASSETS_DIR / "accounts.yaml"
if not _accounts_path.exists():
    raise FileNotFoundError(f"Missing {ASSETS_DIR}/accounts.yaml")

with open(_accounts_path) as f:
    _accounts_config = yaml.safe_load(f)["accounts"]

# Load secrets
_secrets_path = ASSETS_DIR / "accounts.secrets.yaml"
if not _secrets_path.exists():
    raise FileNotFoundError(
        f"\nMissing secrets file: {ASSETS_DIR}/accounts.secrets.yaml\n"
        "→ cp assets/accounts.secrets.example.yaml assets/accounts.secrets.yaml\n"
        "  and fill your real LinkedIn credentials (this file is .gitignore'd)\n"
    )
with open(_secrets_path) as f:
    _secrets = yaml.safe_load(f).get("secrets", {})


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------
def get_account_config(handle: str) -> Dict[str, Any]:
    """One function to rule them all – returns everything needed for an account."""
    if handle not in _accounts_config:
        raise KeyError(f"Account '{handle}' not found in assets/accounts.yaml")

    base = _accounts_config[handle]
    secret = _secrets.get(handle, {})

    return {
        "handle": handle,
        "active": base.get("active", True),
        "display_name": base.get("display_name", handle.replace("_", " ").title()),
        "proxy": base.get("proxy"),
        "daily_connections": base.get("daily_connections", 50),
        "daily_messages": base.get("daily_messages", 20),
        # Credentials
        "username": secret.get("username"),
        "password": secret.get("password"),
        # Runtime paths (all under assets/)
        "cookie_file": COOKIES_DIR / f"{handle}.json",
        "db_path": DATA_DIR / f"linkedin_{handle}.db",
    }


def get_account_db_url(handle: str) -> str:
    """Convenient SQLAlchemy URL for APScheduler"""
    return f"sqlite:///{get_account_config(handle)['db_path']}"


def list_active_accounts() -> list[str]:
    """All accounts marked active (or default true)"""
    return [
        h for h, cfg in _accounts_config.items()
        if cfg.get("active", True)
    ]


# Nice debug print when running directly
if __name__ == "__main__":
    print("LinkedIn Automation – Active accounts:")
    for handle in list_active_accounts():
        cfg = get_account_config(handle)
        print(f"  • {handle}")