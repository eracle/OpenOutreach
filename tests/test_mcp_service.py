from __future__ import annotations

import pytest

from mcp_server.service import ToolError, _ensure_allowed_transition, run_tool


@pytest.mark.parametrize(
    ("current_state", "new_state"),
    [
        ("new", "pending"),
        ("new", "connected"),
        ("pending", "failed"),
        ("connected", "completed"),
        ("completed", "completed"),
    ],
)
def test_allowed_transitions(current_state: str, new_state: str):
    _ensure_allowed_transition(current_state, new_state)


@pytest.mark.parametrize(
    ("current_state", "new_state"),
    [
        ("url_only", "new"),
        ("enriched", "pending"),
        ("completed", "new"),
        ("failed", "connected"),
        ("pending", "new"),
    ],
)
def test_forbidden_transitions_raise(current_state: str, new_state: str):
    with pytest.raises(ToolError) as exc:
        _ensure_allowed_transition(current_state, new_state)
    assert exc.value.code == "invalid_transition"


def test_run_tool_unknown():
    with pytest.raises(ToolError) as exc:
        run_tool("not_a_real_tool", {})
    assert exc.value.code == "unknown_tool"


def test_run_tool_requires_object_arguments():
    with pytest.raises(ToolError) as exc:
        run_tool("get_profile", [])
    assert exc.value.code == "invalid_argument"
