from openoutreach.core.agent_contract import error_response, success_response


def test_success_response_shape():
    payload = success_response(
        command="status",
        result={"campaigns": 1},
        status="succeeded",
    )

    assert payload == {
        "ok": True,
        "command": "status",
        "status": "succeeded",
        "dry_run": False,
        "action_id": None,
        "result": {"campaigns": 1},
        "error": None,
        "warnings": [],
    }


def test_error_response_shape():
    payload = error_response(
        command="email send-next",
        error_type="no_eligible_email",
        message="No eligible READY_TO_EMAIL deal exists.",
    )

    assert payload["ok"] is False
    assert payload["status"] == "failed"
    assert payload["error"] == {
        "type": "no_eligible_email",
        "message": "No eligible READY_TO_EMAIL deal exists.",
    }
