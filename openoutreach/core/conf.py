# openoutreach/core/conf.py
from __future__ import annotations

from pathlib import Path

from openoutreach.core.tz_detect import system_timezone


# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------
ROOT_DIR = Path(__file__).parent.parent.parent

PROMPTS_DIR = Path(__file__).parent / "templates" / "prompts"

DIAGNOSTICS_DIR = Path("/tmp/openoutreach-diagnostics")

FASTEMBED_CACHE_DIR = ROOT_DIR / ".cache" / "fastembed"

MIN_DELAY = 5
MAX_DELAY = 8

# Browser timing/launch knobs and fixture paths now live in
# linkedin_cli/conf.py (the Django-free interaction layer).

# ----------------------------------------------------------------------
# Onboarding defaults (shown to user during interactive setup)
# ----------------------------------------------------------------------
DEFAULT_CONNECT_DAILY_LIMIT = 20
DEFAULT_FOLLOW_UP_DAILY_LIMIT = 25

# Per-mailbox daily email ceiling, set at email onboarding (like the LinkedIn
# connect limit) and stored on each Mailbox. Enforced at send time: the EMAIL
# handler counts a box's outgoing email ChatMessages today and skips a box at
# its cap. Pool throughput is an emergent consequence, never an enforced
# aggregate. 30/day is conservative within the 2026 safe band for a warmed
# Google Workspace box (sources converge on 30–50/day, ~40 a common inbox-level
# hard ceiling, ~25 the cautious floor after the late-2025 deliverability
# crackdown). Reputation damage is the asymmetric risk; scale by adding boxes.
DEFAULT_EMAIL_DAILY_LIMIT = 30

# ----------------------------------------------------------------------
# Active-hours schedule (daemon pauses outside this window)
# Set to False to run 24/7. Working hours are a single contiguous window;
# weekends are no longer special-cased (humans use LinkedIn 7 days a week).
# ----------------------------------------------------------------------
ENABLE_ACTIVE_HOURS = True
ACTIVE_START_HOUR = 9   # inclusive, local time
ACTIVE_END_HOUR = 19    # exclusive, local time
ACTIVE_TIMEZONE = system_timezone()

# ----------------------------------------------------------------------
# Planner cap for check_pending: at most this many lazy slots per 24h
# planning window, regardless of how many PENDING deals are overdue.
# Overflow rolls into the next planning cycle.
# ----------------------------------------------------------------------
CHECK_PENDING_DAILY_CAP = 100

# ----------------------------------------------------------------------
# Campaign config (timing + ML defaults — hardcoded, no YAML)
# ----------------------------------------------------------------------
CAMPAIGN_CONFIG = {
    "check_pending_recheck_after_hours": 24,
    "min_action_interval": 120,
    "qualification_n_mc_samples": 100,
    "min_ready_to_connect_prob": 0.9,
    "min_positive_pool_prob": 0.20,
    "embedding_model": "BAAI/bge-small-en-v1.5",
    "enrich_min_delay_seconds": 6,
    "enrich_max_delay_seconds": 10,
    "enrich_max_per_page": 10,
    "burst_min_seconds": 2700,   # 45 min
    "burst_max_seconds": 3900,   # 65 min
    "break_min_seconds": 600,    # 10 min
    "break_max_seconds": 1200,   # 20 min
}


