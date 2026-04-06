# linkedin/conf.py
from __future__ import annotations

from pathlib import Path


# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------
ROOT_DIR = Path(__file__).parent.parent

PROMPTS_DIR = Path(__file__).parent / "templates" / "prompts"

DIAGNOSTICS_DIR = Path("/tmp/openoutreach-diagnostics")

FASTEMBED_CACHE_DIR = ROOT_DIR / ".cache" / "fastembed"

FIXTURE_DIR = ROOT_DIR / "tests" / "fixtures"
FIXTURE_PROFILES_DIR = FIXTURE_DIR / "profiles"
FIXTURE_PAGES_DIR = FIXTURE_DIR / "pages"
DUMP_PAGES = False

MIN_DELAY = 5
MAX_DELAY = 8

# ----------------------------------------------------------------------
# Browser config
# ----------------------------------------------------------------------
BROWSER_SLOW_MO = 200
BROWSER_DEFAULT_TIMEOUT_MS = 30_000
BROWSER_LOGIN_TIMEOUT_MS = 40_000
BROWSER_NAV_TIMEOUT_MS = 10_000
HUMAN_TYPE_MIN_DELAY_MS = 50
HUMAN_TYPE_MAX_DELAY_MS = 200

# ----------------------------------------------------------------------
# Onboarding defaults (shown to user during interactive setup)
# ----------------------------------------------------------------------
DEFAULT_CONNECT_DAILY_LIMIT = 50
DEFAULT_CONNECT_WEEKLY_LIMIT = 250
DEFAULT_FOLLOW_UP_DAILY_LIMIT = 100

# ----------------------------------------------------------------------
# Active-hours schedule (daemon pauses outside this window)
# Set to False to run 24/7.
# ----------------------------------------------------------------------
ENABLE_ACTIVE_HOURS = False
ACTIVE_START_HOUR = 10   # inclusive, local time
ACTIVE_END_HOUR = 20    # exclusive, local time
ACTIVE_TIMEZONE = "UTC"
REST_DAYS = (5, 6)      # 0=Mon … 6=Sun; default Sat+Sun off

# ----------------------------------------------------------------------
# Campaign config (timing + ML defaults — hardcoded, no YAML)
# ----------------------------------------------------------------------
CAMPAIGN_CONFIG = {
    "check_pending_recheck_after_hours": 24,
    "enrich_min_interval": 1,
    "min_action_interval": 120,
    "qualification_n_mc_samples": 100,
    "min_ready_to_connect_prob": 0.9,
    "min_positive_pool_prob": 0.20,
    "embedding_model": "BAAI/bge-small-en-v1.5",
    "connect_delay_seconds": 10,
    "connect_no_candidate_delay_seconds": 300,
}

# ----------------------------------------------------------------------
# Global LLM config (stored in DB via SiteConfig)
# ----------------------------------------------------------------------

def get_llm_config():
    """Return (llm_provider, llm_api_key, ai_model, llm_api_base) from the DB."""
    from linkedin.models import SiteConfig
    cfg = SiteConfig.load()
    return cfg.llm_provider, cfg.llm_api_key, cfg.ai_model, cfg.llm_api_base or None


def get_llm(temperature: float = 0.7, timeout: int = 60, max_retries: int = 3):
    """Return a LangChain chat model based on the configured provider.

    Includes automatic retry with exponential backoff for rate-limit (429)
    and transient server errors (5xx).
    """
    provider, api_key, model, api_base = get_llm_config()
    if not api_key:
        raise ValueError("LLM_API_KEY is not set in Site Configuration.")

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=model,
            temperature=temperature,
            google_api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
        )
    else:
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            temperature=temperature,
            api_key=api_key,
            base_url=api_base,
            timeout=timeout,
            max_retries=max_retries,
        )

