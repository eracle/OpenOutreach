# linkedin/diagnostics.py
"""Capture page state on automation failures for post-mortem debugging."""
from __future__ import annotations

import logging
import traceback
from contextlib import contextmanager
from datetime import datetime

from linkedin.conf import DIAGNOSTICS_DIR

logger = logging.getLogger(__name__)


def capture_failure(session, error: BaseException) -> None:
    """Save page HTML, screenshot, and error details into a per-failure folder.

    Folder: ``assets/diagnostics/<timestamp>_<ErrorClass>/``
    """
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    error_name = type(error).__name__
    folder = DIAGNOSTICS_DIR / f"{timestamp}_{error_name}"
    folder.mkdir(parents=True, exist_ok=True)

    # Error traceback
    tb = traceback.format_exception(type(error), error, error.__traceback__)
    (folder / "error.txt").write_text("".join(tb))

    page = getattr(session, "page", None)
    if page is None or page.is_closed():
        logger.debug("No live page — skipping HTML/screenshot capture")
        (folder / "page.html").write_text("<!-- page was None or closed -->")
        return

    try:
        (folder / "page.html").write_text(page.content())
    except Exception as exc:
        logger.debug("Failed to capture HTML: %s", exc)

    try:
        page.screenshot(path=str(folder / "screenshot.png"))
    except Exception as exc:
        logger.debug("Failed to capture screenshot: %s", exc)

    logger.info("Failure diagnostics saved → %s", folder)


@contextmanager
def failure_diagnostics(session):
    """Context manager that captures diagnostics on unhandled exceptions."""
    try:
        yield
    except Exception as exc:
        try:
            capture_failure(session, exc)
        except Exception as cap_exc:
            logger.debug("Diagnostic capture itself failed: %s", cap_exc)
        raise
