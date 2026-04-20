# linkedin/tz_detect.py
"""System timezone detection for the active-hours daemon schedule.

Each `_tz_from_*` source returns an IANA name (e.g. "Europe/Rome") or
None. `system_timezone()` tries them in order and validates each
candidate against the tzdata database; falls back to "UTC" on exotic
systems.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _tz_from_python() -> str | None:
    """IANA name from Python's tzinfo (set when tzdata resolves to a ZoneInfo)."""
    return getattr(datetime.now().astimezone().tzinfo, "key", None)


def _tz_from_env() -> str | None:
    """IANA name from the TZ env var (commonly set in Docker).

    Strips the POSIX ``:IANA`` leading-colon convention and surrounding
    whitespace, so ``TZ=:Europe/Rome`` resolves the same as ``TZ=Europe/Rome``.
    """
    raw = os.environ.get("TZ") or ""
    value = raw.lstrip(":").strip()
    return value or None


def _tz_from_etc_timezone() -> str | None:
    """IANA name from /etc/timezone (Debian/Ubuntu/most Linux)."""
    try:
        return Path("/etc/timezone").read_text().strip() or None
    except OSError:
        return None


def _tz_from_etc_localtime() -> str | None:
    """IANA name parsed from /etc/localtime symlink target (RHEL/Alpine/macOS)."""
    try:
        target = os.readlink("/etc/localtime")
    except OSError:
        return None
    marker = "/zoneinfo/"
    idx = target.rfind(marker)
    return target[idx + len(marker):] if idx >= 0 else None


def _is_valid_iana(name: str) -> bool:
    """True iff ``name`` resolves in the installed tzdata."""
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


_SOURCES = (
    _tz_from_python,
    _tz_from_env,
    _tz_from_etc_timezone,
    _tz_from_etc_localtime,
)


def system_timezone() -> str:
    """Best-effort system IANA timezone (e.g. 'Europe/Rome'); falls back to 'UTC'."""
    for source in _SOURCES:
        name = source()
        if name and _is_valid_iana(name):
            return name
    return "UTC"
