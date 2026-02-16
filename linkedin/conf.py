# linkedin/conf.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Any, List

import yaml
from dotenv import load_dotenv

load_dotenv()

# ----------------------------------------------------------------------
# Paths (all under assets/)
# ----------------------------------------------------------------------
ROOT_DIR = Path(__file__).parent.parent
ASSETS_DIR = ROOT_DIR / "assets"

COOKIES_DIR = ASSETS_DIR / "cookies"
DATA_DIR = ASSETS_DIR / "data"
CAMPAIGN_DIR = ASSETS_DIR / "campaign"
PRODUCT_DOCS_FILE = CAMPAIGN_DIR / "product_docs.txt"
CAMPAIGN_OBJECTIVE_FILE = CAMPAIGN_DIR / "campaign_objective.txt"

EMBEDDINGS_DB = DATA_DIR / "analytics.duckdb"

COOKIES_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

FIXTURE_DIR = ROOT_DIR / "tests" / "fixtures"
FIXTURE_PROFILES_DIR = FIXTURE_DIR / "profiles"
FIXTURE_PAGES_DIR = FIXTURE_DIR / "pages"

MIN_DELAY = 5
MAX_DELAY = 8

# ----------------------------------------------------------------------
# SINGLE secrets file
# ----------------------------------------------------------------------
SECRETS_PATH = ASSETS_DIR / "accounts.secrets.yaml"

if SECRETS_PATH.exists():
    with open(SECRETS_PATH, "r", encoding="utf-8") as f:
        _raw_config = yaml.safe_load(f) or {}
else:
    import warnings
    warnings.warn(
        f"Missing config file: {SECRETS_PATH}\n"
        "→ cp assets/accounts.secrets.template.yaml assets/accounts.secrets.yaml\n"
        "  Using defaults — the daemon will not start without a config file.",
        stacklevel=2,
    )
    _raw_config = {}

_accounts_config = _raw_config.get("accounts", {})

# ----------------------------------------------------------------------
# Campaign config (rate limits, timing)
# ----------------------------------------------------------------------
_campaign_raw = _raw_config.get("campaign", {}) or {}

_connect_cfg = _campaign_raw.get("connect", {}) or {}
_check_cfg = _campaign_raw.get("check_pending", {}) or {}
_followup_cfg = _campaign_raw.get("follow_up", {}) or {}

_schedule_cfg = _campaign_raw.get("working_hours", {}) or {}
_qualification_cfg = _campaign_raw.get("qualification", {}) or {}

CAMPAIGN_CONFIG = {
    "connect_daily_limit": _connect_cfg.get("daily_limit", 20),
    "connect_weekly_limit": _connect_cfg.get("weekly_limit", 100),
    "check_pending_recheck_after_hours": _check_cfg.get("recheck_after_hours", 24),
    "follow_up_daily_limit": _followup_cfg.get("daily_limit", 30),
    "follow_up_existing_connections": _followup_cfg.get("existing_connections", False),
    "working_hours_start": _schedule_cfg.get("start", "09:00"),
    "working_hours_end": _schedule_cfg.get("end", "18:00"),
    "enrich_min_interval": _campaign_raw.get("enrich_min_interval", 1),
    "min_action_interval": _campaign_raw.get("min_action_interval", 120),
    "qualification_entropy_threshold": _qualification_cfg.get("entropy_threshold", 0.3),
    "qualification_n_mc_samples": _qualification_cfg.get("n_mc_samples", 100),
    "embedding_model": _qualification_cfg.get("embedding_model", "BAAI/bge-small-en-v1.5"),
}

# ----------------------------------------------------------------------
# Global OpenAI / LLM config
# ----------------------------------------------------------------------
# Loaded from gitignored accounts.secrets.yaml/env section first,
# then .env / os.getenv fallback. Defaults applied if missing.
env_config = _raw_config.get("env", {}) or {}

LLM_API_KEY = env_config.get("LLM_API_KEY") or os.getenv("LLM_API_KEY")
LLM_API_BASE = env_config.get("LLM_API_BASE") or os.getenv("LLM_API_BASE")
AI_MODEL = env_config.get("AI_MODEL") or os.getenv("AI_MODEL", "gpt-5.3-codex")  # latest frontier agentic model (Feb 2026 release)

if not LLM_API_KEY:
    import warnings
    warnings.warn(
        "LLM_API_KEY is not set. LLM features will not work.\n"
        "Add it under the 'env:' section in accounts.secrets.yaml, e.g.:\n"
        "env:\n  LLM_API_KEY: sk-...\n"
        "or set it via .env file or environment variable.",
        stacklevel=2,
    )

# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------
def get_account_config(handle: str) -> Dict[str, Any]:
    if handle not in _accounts_config:
        raise KeyError(f"Account '{handle}' not found in {SECRETS_PATH}")

    acct = _accounts_config[handle]

    followup_rel = acct.get("followup_template") or "templates/prompts/followup2.j2"

    return {
        "handle": handle,
        "active": acct.get("active", True),
        "username": acct.get("username"),
        "password": acct.get("password"),
        "subscribe_newsletter": acct.get("subscribe_newsletter", None),
        "booking_link": acct.get("booking_link"),

        "cookie_file": COOKIES_DIR / f"{handle}.json",

        "followup_template": ASSETS_DIR / followup_rel,
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
    The order is deterministic (follows insertion order in YAML).
    """
    active = list_active_accounts()
    return active[0] if active else None

def get_first_account_config() -> Dict[str, Any] | None:
    """Return the complete config dict for the first active account, or None."""
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
    print("-" * 60)

    active_handles = list_active_accounts()
    if not active_handles:
        print("No active accounts found.")
    else:
        for handle in active_handles:
            cfg = get_account_config(handle)
            status = "ACTIVE" if cfg["active"] else "inactive"
            print(f"{status} • {handle}")
            print("  Config values:")
            for key, value in cfg.items():
                if isinstance(value, Path):
                    value = value.as_posix()
                elif value is None:
                    value = "null"
                print(f"    {key.ljust(20)} : {value}")
            print()

        print("-" * 60)
        first = get_first_active_account()
        print(f"First active account → {first or 'None'}")