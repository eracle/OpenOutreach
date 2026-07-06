# openoutreach/core/conf.py
from __future__ import annotations

from pathlib import Path


# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------
ROOT_DIR = Path(__file__).parent.parent.parent

PROMPTS_DIR = Path(__file__).parent / "templates" / "prompts"

FASTEMBED_CACHE_DIR = ROOT_DIR / ".cache" / "fastembed"

# ----------------------------------------------------------------------
# Onboarding defaults (shown to user during interactive setup)
# ----------------------------------------------------------------------
# Per-mailbox daily email ceiling, set at email onboarding and stored on each
# Mailbox. Enforced at send time: the EMAIL
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
# weekends are not special-cased.
#
# ACTIVE_TIMEZONE is None by default: the window timezone is resolved at
# runtime from the operator's onboarding country (SiteConfig.country_code;
# see OperatorSession.active_timezone). Set it to an IANA name here (or via
# config) to pin the window explicitly.
# ----------------------------------------------------------------------
ENABLE_ACTIVE_HOURS = False
ACTIVE_START_HOUR = 9   # inclusive, local time
ACTIVE_END_HOUR = 19    # exclusive, local time
ACTIVE_TIMEZONE = None  # None → resolve from the operator's onboarding country

# ----------------------------------------------------------------------
# Planner cap for find_email: at most this many BetterContact lookups per
# 24h planning window (the paid-action spend guard, since a verified hit
# costs one credit). Slots that find no ranked candidate no-op; overflow
# rolls into the next planning cycle.
# ----------------------------------------------------------------------
FIND_EMAIL_DAILY_CAP = 50

# ----------------------------------------------------------------------
# Campaign config (timing + ML defaults — hardcoded, no YAML)
# ----------------------------------------------------------------------
CAMPAIGN_CONFIG = {
    "qualification_n_mc_samples": 100,
    # GP confidence gate: P(f>0.5) above this promotes QUALIFIED → READY_TO_FIND_EMAIL
    # (rations the paid BetterContact lookup to leads the model is confident about).
    "min_gp_confidence": 0.9,
    "embedding_model": "BAAI/bge-small-en-v1.5",
    "burst_min_seconds": 2700,   # 45 min
    "burst_max_seconds": 3900,   # 65 min
    "break_min_seconds": 600,    # 10 min
    "break_max_seconds": 1200,   # 20 min
}


