# linkedin/logging.py
"""Centralized logging configuration with colored output and startup banner."""
from __future__ import annotations

import logging
import sys

from termcolor import colored

# ── Banner ──────────────────────────────────────────────────────────

BANNER = r"""
   ___                   ___        _                      _
  / _ \ _ __   ___ _ __ / _ \ _   _| |_ _ __ ___  __ _  ___| |__
 | | | | '_ \ / _ \ '_ \ | | | | | | __| '__/ _ \/ _` |/ __| '_ \
 | |_| | |_) |  __/ | | | |_| | |_| | |_| | |  __/ (_| | (__| | | |
  \___/| .__/ \___|_| |_|\___/ \__,_|\__|_|  \___|\__,_|\___|_| |_|
       |_|
"""


def print_banner():
    """Print the OpenOutreach startup banner in bold cyan."""
    sys.stdout.write(colored(BANNER, "cyan", attrs=["bold"]))
    sys.stdout.write("\n")
    sys.stdout.flush()


# ── Colored formatter ───────────────────────────────────────────────

_LEVEL_COLORS = {
    logging.DEBUG: ("dark_grey", []),
    logging.INFO: (None, []),
    logging.WARNING: ("yellow", ["bold"]),
    logging.ERROR: ("red", ["bold"]),
    logging.CRITICAL: ("red", ["bold", "underline"]),
}

_LEVEL_LABELS = {
    logging.DEBUG: "DBG",
    logging.INFO: "INF",
    logging.WARNING: "WRN",
    logging.ERROR: "ERR",
    logging.CRITICAL: "CRT",
}


class ColoredFormatter(logging.Formatter):
    """Compact colored formatter: ``[LVL] message``."""

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        color, attrs = _LEVEL_COLORS.get(record.levelno, (None, []))
        label = _LEVEL_LABELS.get(record.levelno, "???")
        prefix = colored(f"[{label}]", color, attrs=attrs) if color else f"[{label}]"
        return f"{prefix} {msg}"


# ── Public API ──────────────────────────────────────────────────────

SILENCED_LOGGERS = (
    "urllib3", "httpx", "pydantic_ai", "openai", "playwright",
    "httpcore", "fastembed", "huggingface_hub", "filelock", "asyncio",
)


def configure_logging(level: int = logging.DEBUG):
    """Configure root logger with colored output and silence noisy libraries."""
    root = logging.getLogger()
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ColoredFormatter("%(message)s"))
    handler.setLevel(level)

    root.addHandler(handler)
    root.setLevel(level)

    for name in SILENCED_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
