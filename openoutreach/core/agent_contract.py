from __future__ import annotations

from typing import Any


def success_response(
    *,
    command: str,
    result: dict[str, Any],
    status: str = "succeeded",
    dry_run: bool = False,
    action_id: int | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "command": command,
        "status": status,
        "dry_run": dry_run,
        "action_id": action_id,
        "result": result,
        "error": None,
        "warnings": warnings or [],
    }


def error_response(
    *,
    command: str,
    error_type: str,
    message: str,
    status: str = "failed",
    dry_run: bool = False,
    action_id: int | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "command": command,
        "status": status,
        "dry_run": dry_run,
        "action_id": action_id,
        "result": None,
        "error": {
            "type": error_type,
            "message": message,
        },
        "warnings": warnings or [],
    }
