# openoutreach/core/logging.py
"""Centralized logging configuration with colored output and startup banner."""
from __future__ import annotations

import logging
import os
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
    """Print the OpenOutreach startup banner in bold cyan — on stderr.

    The banner is decoration, not a result. Everything decorative shares stderr with
    the logs so that stdout carries only what a program came for.
    """
    sys.stderr.write(colored(BANNER, "cyan", attrs=["bold"]))
    sys.stderr.write("\n")
    sys.stderr.flush()


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


# ── Brand palette (third-party services) ────────────────────────────
# 24-bit accent colours lifted from each vendor's own site, so a service
# name prints in its real palette colour. termcolor only knows the 16
# named colours, so these go out as raw truecolor SGR escapes.

_BRANDS = {
    "bettercontact": ("BetterContact", (155, 81, 224)),  # bettercontact.rocks #9b51e0
    "apollo": ("Apollo", (58, 106, 255)),  # apollo.io #3a6aff
    "icemail": ("IceMail", (34, 197, 94)),               # icemail.ai --brand #22c55e
}


def _color_enabled() -> bool:
    """Mirror termcolor's gating: NO_COLOR off, FORCE_COLOR on, else TTY-only.

    Gated on **stderr**, because that is where the coloured output goes. Gating on
    stdout would strip the colour out of an interactive run the moment its result was
    piped somewhere.
    """
    if "NO_COLOR" in os.environ:
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stderr.isatty()


def brand(service: str, text: str | None = None) -> str:
    """Render a service name (or `text`) in that vendor's brand colour."""
    label, (r, g, b) = _BRANDS[service]
    label = text if text is not None else label
    if not _color_enabled():
        return label
    return f"\033[38;2;{r};{g};{b}m{label}\033[0m"


def format_elapsed(seconds: float) -> str:
    """A duration read at a glance: ``52s``, ``4m09s``, ``1h04m``.

    Milestones carry elapsed time because a run that takes minutes has to say how many —
    it is also how the first-run number stays measured rather than measured once.
    """
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def hyperlink(url: str, text: str | None = None) -> str:
    """Render `text` (default: the URL) as an OSC 8 clickable terminal link.

    On a non-TTY (or NO_COLOR) we return the plain URL so piped logs stay
    clean; terminals without OSC 8 support ignore the escapes and still show
    the label.
    """
    label = text if text is not None else url
    if not _color_enabled():
        return label
    return f"\033]8;;{url}\033\\{label}\033]8;;\033\\"


# ── Public API ──────────────────────────────────────────────────────

# Libraries whose own DEBUG output would bury ours. Held at WARNING no matter what level
# the daemon runs at — the point of ``--log-level debug`` is to see the engine reason, and
# every LLM SDK dumps the full request body at DEBUG (``Request options: {'method': ...}``,
# one screen per call).
#
# **Every provider in ``llm._PROVIDER_BUILDERS`` needs its SDK here**, not just the one
# currently configured: the list is invisible until someone switches models and then finds
# their debug run unreadable. ``tests/test_llm.py`` asserts the two stay in step, because
# this is exactly the kind of list that rots silently.
SILENCED_LOGGERS = (
    # LLM SDKs — one entry per supported provider
    "openai", "anthropic", "google", "google_genai", "googleapiclient",
    "groq", "mistralai", "cohere", "pydantic_ai",
    # HTTP transports underneath them — httpx2/httpcore2 are the renamed transport
    # the anthropic SDK vendors; same noise, different logger names.
    "urllib3", "httpx", "httpcore", "httpx2", "httpcore2", "h11", "hpack",
    # Embeddings + runtime
    "fastembed", "huggingface_hub", "filelock", "onnxruntime", "asyncio",
)


def resolve_log_level(log_level: str | None, verbosity: int) -> int:
    """``--log-level`` wins; Django's ``-v 2`` stays as the shorthand for debug."""
    if log_level:
        return getattr(logging, log_level.upper())
    return logging.DEBUG if verbosity >= 2 else logging.INFO


def _pin_termcolor_to_stderr() -> None:
    """Make every ``colored()`` call in the codebase key off stderr, not stdout.

    termcolor's own ``can_colorize()`` gates on ``sys.stdout.isatty()`` and caches
    the answer forever (``@cache``) — wrong stream for a tool where every colored
    call (logs, the banner, ``qualify``'s per-lead status) writes to stderr, and a
    one-shot cache that would freeze in the answer from whichever stream happened
    to get checked first. ``find ... > leads.csv`` redirects stdout but leaves
    stderr a live terminal, so without this every colored call goes dark the
    moment stdout stops being a TTY. Only acts if the operator hasn't already
    forced an answer via ``NO_COLOR``/``FORCE_COLOR``, and runs before any
    ``colored()`` call so the cached decision is the right one from the start.
    """
    if "NO_COLOR" in os.environ or "FORCE_COLOR" in os.environ:
        return
    os.environ["FORCE_COLOR" if _color_enabled() else "NO_COLOR"] = "1"


def configure_logging(level: int = logging.INFO):
    """Configure root logger with colored output and silence noisy libraries."""
    _pin_termcolor_to_stderr()

    root = logging.getLogger()
    root.handlers.clear()

    # stderr, not stdout: logs are not the result. A run whose stdout is redirected
    # into a file or piped into a program must yield data and nothing else.
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(ColoredFormatter("%(message)s"))
    handler.setLevel(level)

    root.addHandler(handler)
    root.setLevel(level)

    for name in SILENCED_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
