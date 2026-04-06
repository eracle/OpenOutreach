# linkedin/daemon_manager.py
"""Web-controllable daemon manager.

Allows starting/stopping daemon threads per LinkedIn profile from the CRM UI.
Each profile gets its own daemon thread with a stop event.
"""
from __future__ import annotations

import logging
import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class DaemonState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class DaemonInfo:
    profile_pk: int
    profile_name: str
    state: DaemonState = DaemonState.STOPPED
    thread: threading.Thread | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    started_at: datetime | None = None
    error: str = ""
    campaign_names: list[str] = field(default_factory=list)


# Module-level registry of daemon threads per profile PK
_daemons: dict[int, DaemonInfo] = {}
_lock = threading.Lock()


def get_daemon_info(profile_pk: int) -> DaemonInfo | None:
    return _daemons.get(profile_pk)


def get_all_daemons() -> dict[int, DaemonInfo]:
    return dict(_daemons)


def is_running(profile_pk: int) -> bool:
    info = _daemons.get(profile_pk)
    return info is not None and info.state in (DaemonState.RUNNING, DaemonState.STARTING)


def start_daemon(profile_pk: int) -> tuple[bool, str]:
    """Start daemon for a LinkedIn profile. Returns (success, message)."""
    with _lock:
        existing = _daemons.get(profile_pk)
        if existing and existing.state in (DaemonState.RUNNING, DaemonState.STARTING):
            return False, "Daemon is already running for this profile."

        # Wait for old thread to fully exit before starting a new one
        if existing and existing.thread and existing.thread.is_alive():
            logger.info("Waiting for old daemon thread to exit for profile %s", profile_pk)
            existing.stop_event.set()
            existing.thread.join(timeout=10)
            if existing.thread.is_alive():
                return False, "Previous daemon thread is still shutting down. Try again."

        from linkedin.models import LinkedInProfile
        try:
            profile = LinkedInProfile.objects.select_related("user").get(pk=profile_pk)
        except LinkedInProfile.DoesNotExist:
            return False, "LinkedIn profile not found."

        if not profile.active:
            return False, "Profile is inactive. Activate it first."

        info = DaemonInfo(
            profile_pk=profile_pk,
            profile_name=str(profile),
            state=DaemonState.STARTING,
            stop_event=threading.Event(),
        )
        _daemons[profile_pk] = info

        thread = threading.Thread(
            target=_run_daemon_thread,
            args=(profile_pk, info),
            name=f"daemon-{profile.user.username}",
            daemon=True,
        )
        info.thread = thread
        thread.start()
        logger.info("Daemon thread spawned for profile %s (%s)", profile_pk, profile)

    return True, f"Daemon starting for {profile}."


def stop_daemon(profile_pk: int) -> tuple[bool, str]:
    """Signal daemon to stop. Returns (success, message)."""
    with _lock:
        info = _daemons.get(profile_pk)
        if info is None or info.state in (DaemonState.STOPPED, DaemonState.ERROR):
            return False, "Daemon is not running."

        info.state = DaemonState.STOPPING
        info.stop_event.set()

    return True, "Stop signal sent. Daemon will stop after current task."


def _run_daemon_thread(profile_pk: int, info: DaemonInfo):
    """Entry point for daemon thread. Sets up session and runs daemon loop."""
    from django import db

    try:
        logger.info("[daemon:%s] Thread started", profile_pk)

        from linkedin.browser.registry import _sessions
        from linkedin.browser.session import AccountSession
        from linkedin.conf import get_llm_config
        from linkedin.models import LinkedInProfile

        profile = LinkedInProfile.objects.select_related("user").get(pk=profile_pk)
        logger.info("[daemon:%s] Profile loaded: %s (active=%s)", profile_pk, profile, profile.active)

        llm_api_key = get_llm_config()[1]
        if not llm_api_key:
            info.state = DaemonState.ERROR
            info.error = "LLM API key not configured. Go to Settings to add one."
            logger.error("[daemon:%s] %s", profile_pk, info.error)
            return

        # Always create a fresh session so campaign assignments are current.
        # Preserve browser objects from any existing session to avoid re-login.
        old_session = _sessions.get(profile_pk)
        session = AccountSession(profile)
        if old_session and old_session.page and not old_session.page.is_closed():
            session.page = old_session.page
            session.context = old_session.context
            session.browser = old_session.browser
            session.playwright = old_session.playwright
        _sessions[profile_pk] = session

        logger.info("[daemon:%s] Session created (fresh)", profile_pk)

        if not session.campaigns:
            info.state = DaemonState.ERROR
            info.error = "No campaigns assigned to this profile's user. Assign a campaign first."
            logger.error("[daemon:%s] %s", profile_pk, info.error)
            return

        info.campaign_names = [c.name for c in session.campaigns]
        logger.info("[daemon:%s] Campaigns: %s", profile_pk, info.campaign_names)

        campaign = session.campaigns[0]
        session.campaign = campaign

        info.state = DaemonState.RUNNING
        info.started_at = datetime.now()
        logger.info("[daemon:%s] State → RUNNING", profile_pk)

        from linkedin.daemon import run_daemon
        run_daemon(session, stop_event=info.stop_event)

        logger.info("[daemon:%s] run_daemon returned normally", profile_pk)

    except Exception:
        from linkedin.daemon import friendly_error
        raw = traceback.format_exc()
        info.error = friendly_error(raw)
        info.state = DaemonState.ERROR
        logger.exception("[daemon:%s] Thread crashed", profile_pk)
    else:
        info.state = DaemonState.STOPPED
    finally:
        info.stop_event.clear()
        db.connections.close_all()
        logger.info("[daemon:%s] Thread exited (state=%s)", profile_pk, info.state.value)
