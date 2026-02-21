"""MCP stdio server for OpenOutreach."""
from __future__ import annotations

import json
import logging
import sys
from typing import Any

from mcp_server.bootstrap import setup_django

logger = logging.getLogger(__name__)

SERVER_INFO = {"name": "openoutreach-mcp", "version": "0.1.0"}
PROTOCOL_VERSION = "2025-11-05"

TOOLS = [
    {
        "name": "get_pipeline_stats",
        "description": "Get campaign pipeline and ML labeling counters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "handle": {"type": "string"},
                "campaign": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "list_profiles_by_state",
        "description": "List profiles filtered by state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "handle": {"type": "string"},
                "campaign": {"type": "string"},
                "state": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["state"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_profile",
        "description": "Get full profile payload and computed state by public identifier.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "handle": {"type": "string"},
                "campaign": {"type": "string"},
                "public_identifier": {"type": "string"},
            },
            "required": ["public_identifier"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_qualification_reason",
        "description": "Get stored qualification reason from embeddings store.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "public_identifier": {"type": "string"},
            },
            "required": ["public_identifier"],
            "additionalProperties": False,
        },
    },
    {
        "name": "render_followup_preview",
        "description": "Render follow-up preview using campaign template (or override).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "handle": {"type": "string"},
                "campaign": {"type": "string"},
                "public_identifier": {"type": "string"},
                "template_override": {"type": "string"},
            },
            "required": ["public_identifier"],
            "additionalProperties": False,
        },
    },
    {
        "name": "set_profile_state",
        "description": "Set profile deal state with strict transition validation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "handle": {"type": "string"},
                "campaign": {"type": "string"},
                "public_identifier": {"type": "string"},
                "new_state": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["public_identifier", "new_state"],
            "additionalProperties": False,
        },
    },
]


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _read_message() -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if line == b"":
            return None
        if line in (b"\r\n", b"\n"):
            break
        key, _, value = line.decode("utf-8").partition(":")
        headers[key.strip().lower()] = value.strip()

    content_length = headers.get("content-length")
    if not content_length:
        raise JsonRpcError(-32700, "Missing Content-Length header.")

    try:
        n_bytes = int(content_length)
    except ValueError as exc:
        raise JsonRpcError(-32700, "Invalid Content-Length header.") from exc

    body = sys.stdin.buffer.read(n_bytes)
    if len(body) != n_bytes:
        raise JsonRpcError(-32700, "Incomplete request body.")
    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise JsonRpcError(-32700, "Invalid JSON body.") from exc


def _write_message(payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
    sys.stdout.buffer.write(header)
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def _success_response(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error_response(req_id: Any, code: int, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        payload["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": payload}


def _as_tool_result(payload: dict[str, Any], is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "isError": is_error,
    }


def _handle_request(
    message: dict[str, Any],
    run_tool_fn,
    tool_error_cls,
) -> dict[str, Any] | None:
    method = message.get("method")
    req_id = message.get("id")
    params = message.get("params", {})

    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return _success_response(
            req_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method == "tools/list":
        return _success_response(req_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not name:
            return _success_response(
                req_id,
                _as_tool_result(
                    {"code": "invalid_argument", "message": "Tool name is required."},
                    is_error=True,
                ),
            )
        try:
            result = run_tool_fn(name, arguments)
            return _success_response(req_id, _as_tool_result(result))
        except tool_error_cls as exc:
            return _success_response(req_id, _as_tool_result(exc.as_dict(), is_error=True))
        except Exception as exc:  # noqa: BLE001 - report safe generic error to MCP client
            logger.exception("Unhandled error while executing tool %s", name)
            return _success_response(
                req_id,
                _as_tool_result(
                    {"code": "internal_error", "message": str(exc)},
                    is_error=True,
                ),
            )

    raise JsonRpcError(-32601, f"Method not found: {method}")


def serve_forever() -> None:
    setup_django()
    from mcp_server.service import ToolError, run_tool

    while True:
        req_id = None
        try:
            message = _read_message()
            if message is None:
                return
            req_id = message.get("id")
            response = _handle_request(message, run_tool, ToolError)
            if response is not None and "id" in message:
                _write_message(response)
        except JsonRpcError as exc:
            response = _error_response(req_id, exc.code, exc.message, exc.data)
            _write_message(response)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Fatal server loop error")
            response = _error_response(None, -32603, "Internal error", {"detail": str(exc)})
            _write_message(response)
