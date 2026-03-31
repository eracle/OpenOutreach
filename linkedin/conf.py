# linkedin/conf.py
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------
ROOT_DIR = Path(__file__).parent.parent

PROMPTS_DIR = Path(__file__).parent / "templates" / "prompts"

DIAGNOSTICS_DIR = Path("/tmp/openoutreach-diagnostics")

ENV_FILE = ROOT_DIR / ".env"

FIXTURE_DIR = ROOT_DIR / "tests" / "fixtures"
FIXTURE_PROFILES_DIR = FIXTURE_DIR / "profiles"
FIXTURE_PAGES_DIR = FIXTURE_DIR / "pages"

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
VOYAGER_REQUEST_TIMEOUT_MS = 30_000

# ----------------------------------------------------------------------
# Onboarding defaults (shown to user during interactive setup)
# ----------------------------------------------------------------------
DEFAULT_CONNECT_DAILY_LIMIT = 20
DEFAULT_CONNECT_WEEKLY_LIMIT = 100
DEFAULT_FOLLOW_UP_DAILY_LIMIT = 30
MAX_TOTAL_DAILY_ACTIONS = int(os.getenv("MAX_TOTAL_DAILY_ACTIONS", "200"))

# ----------------------------------------------------------------------
# Active-hours schedule (daemon pauses outside this window)
# Set to False to run 24/7.
# ----------------------------------------------------------------------
ENABLE_ACTIVE_HOURS = os.getenv("ENABLE_ACTIVE_HOURS", "true").strip().lower() in {
    "1", "true", "yes", "on",
}
ACTIVE_START_HOUR = int(os.getenv("ACTIVE_START_HOUR", "9"))   # inclusive, local time
ACTIVE_END_HOUR = int(os.getenv("ACTIVE_END_HOUR", "17"))     # exclusive, local time
ACTIVE_TIMEZONE = os.getenv("ACTIVE_TIMEZONE", "America/Toronto")
REST_DAYS = tuple(
    int(day.strip()) for day in os.getenv("REST_DAYS", "5,6").split(",") if day.strip()
)      # 0=Mon … 6=Sun; default Sat+Sun off

# ----------------------------------------------------------------------
# Campaign config (timing + ML defaults — hardcoded, no YAML)
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# Connection note templates (sent with connection requests)
# ----------------------------------------------------------------------
CONNECTION_NOTE_PERSONALIZED = (
    "Hi {first_name}, after 100+ engineering interviews I got tired of "
    "high-volume resumes inflating costs and burning out teams.\n\n"
    "Built a pre-screening system that filters thousands into a targeted "
    "shortlist, this cuts cost per hire and scales your TA capacity.\n\n"
    "Want the quick demo?"
)

CONNECTION_NOTE_FALLBACK = (
    "Hi, after 100+ engineering interviews I got tired of slow screening "
    "dragging out time to hire.\n\n"
    "Built a pre-screening system that filters thousands on actual "
    "engineering ability, this accelerates your pipeline without "
    "sacrificing quality.\n\n"
    "Want the quick demo?"
)

# Path to GIF/image to attach to follow-up messages (empty = disabled).
# Relative paths are resolved from the repo root so the same .env works
# across machines without requiring identical checkout locations.
_raw_follow_up_media_path = os.getenv("FOLLOW_UP_MEDIA_PATH", "").strip()
if _raw_follow_up_media_path:
    _follow_up_media_path = Path(_raw_follow_up_media_path).expanduser()
    if not _follow_up_media_path.is_absolute():
        _follow_up_media_path = ROOT_DIR / _follow_up_media_path
    FOLLOW_UP_MEDIA_PATH = str(_follow_up_media_path)
else:
    FOLLOW_UP_MEDIA_PATH = ""

# Tracked walkthrough link sent after a connection is accepted without a reply.
# Empty = keep the generic follow-up agent behavior.
POST_ACCEPT_VIDEO_LINK = os.getenv("POST_ACCEPT_VIDEO_LINK", "")
POST_ACCEPT_MESSAGE_TEMPLATE = os.getenv(
    "POST_ACCEPT_MESSAGE_TEMPLATE",
    "Hey {first_name} - put together a 60-second walkthrough of what I mentioned. "
    "Easier to show than explain: {video_link}",
)

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
    "check_pending_jitter_factor": 0.2,
}

# ----------------------------------------------------------------------
# Global OpenAI / LLM config
# ----------------------------------------------------------------------
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_API_BASE = os.getenv("LLM_API_BASE")
AI_MODEL = os.getenv("AI_MODEL")

# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------


def get_first_active_profile_handle() -> str | None:
    """Return the username of the first active LinkedInProfile, or None."""
    from linkedin.models import LinkedInProfile

    profile = LinkedInProfile.objects.filter(active=True).select_related("user").first()
    return profile.user.username if profile else None
