from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from openoutreach.core.agents.email_opener import EmailDraft
from openoutreach.core.models import ActionLog, Campaign, Task
from openoutreach.crm.models import DealState
from openoutreach.emails.models import Mailbox
from tests.factories import DealFactory, LeadFactory, UserFactory

pytestmark = pytest.mark.django_db


def _oo(*args: str) -> dict:
    stdout = StringIO()
    call_command("oo", *args, stdout=stdout)
    return json.loads(stdout.getvalue())


def _json_value(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def _campaign_with_operator(name="Email Campaign"):
    user = UserFactory(email="operator@example.com")
    campaign = Campaign.objects.create(name=name)
    campaign.users.add(user)
    return campaign, user


def _box(email="sender@example.com", daily_limit=10):
    return Mailbox.objects.create(
        username=email,
        password="pw",
        from_address=email,
        daily_limit=daily_limit,
    )


def _ready(campaign, email="lead@example.com"):
    return DealFactory(
        campaign=campaign,
        lead=LeadFactory(email=email),
        state=DealState.READY_TO_EMAIL,
    )


def _send_mocks():
    return patch(
        "openoutreach.core.db.summaries.materialize_profile_summary_if_missing",
    ), patch(
        "openoutreach.core.agents.email_opener.compose_opener_email",
        return_value=EmailDraft(subject="Hi there", body="Short opener.", follow_up_hours=48),
    ), patch(
        "openoutreach.emails.sender.send_email",
        return_value="<mid@example.com>",
    )


def test_status_returns_monitoring_counts():
    campaign = Campaign.objects.create(name="Status Campaign")
    LeadFactory()
    DealFactory(campaign=campaign)
    Task.objects.create(
        task_type=Task.TaskType.FIND_EMAIL,
        status=Task.Status.PENDING,
        scheduled_at=timezone.now(),
    )
    Task.objects.create(
        task_type=Task.TaskType.EMAIL,
        status=Task.Status.RUNNING,
        scheduled_at=timezone.now(),
    )
    Task.objects.create(
        task_type=Task.TaskType.FOLLOW_UP,
        status=Task.Status.FAILED,
        scheduled_at=timezone.now(),
    )

    payload = _oo("status", "--json")

    assert payload["ok"] is True
    assert payload["command"] == "status"
    assert payload["result"] == {
        "campaigns": 1,
        "leads": 2,
        "deals": 1,
        "pending_tasks": 1,
        "running_tasks": 1,
        "failed_tasks": 1,
    }


def test_campaign_list_returns_campaign_rows():
    user = UserFactory()
    campaign = Campaign.objects.create(name="A Campaign", discovery_offset=12)
    campaign.users.add(user)
    DealFactory(campaign=campaign)
    Campaign.objects.create(name="B Campaign", discovery_offset=4)

    payload = _oo("campaign", "list", "--json")

    assert payload["ok"] is True
    assert payload["command"] == "campaign list"
    assert payload["result"]["campaigns"] == [
        {
            "id": campaign.id,
            "name": "A Campaign",
            "users_count": 1,
            "deal_count": 1,
            "discovery_offset": 12,
        },
        {
            "id": Campaign.objects.get(name="B Campaign").id,
            "name": "B Campaign",
            "users_count": 0,
            "deal_count": 0,
            "discovery_offset": 4,
        },
    ]


def test_lead_list_filters_by_campaign_and_state():
    campaign = Campaign.objects.create(name="Filtered Campaign")
    other_campaign = Campaign.objects.create(name="Other Campaign")
    included_lead = LeadFactory(
        profile_url="https://www.linkedin.com/in/alice/",
        email="alice@example.com",
        disqualified=True,
    )
    included_deal = DealFactory(
        campaign=campaign,
        lead=included_lead,
        state=DealState.READY_TO_EMAIL,
    )
    DealFactory(campaign=campaign, state=DealState.QUALIFIED)
    DealFactory(campaign=other_campaign, state=DealState.READY_TO_EMAIL)

    payload = _oo(
        "lead",
        "list",
        "--campaign",
        "Filtered Campaign",
        "--state",
        DealState.READY_TO_EMAIL,
        "--json",
    )

    assert payload["ok"] is True
    assert payload["command"] == "lead list"
    assert payload["result"]["campaign"] == "Filtered Campaign"
    assert payload["result"]["state"] == DealState.READY_TO_EMAIL.value
    assert payload["result"]["leads"] == [
        {
            "deal_id": included_deal.id,
            "lead_id": included_lead.id,
            "profile_url": "https://www.linkedin.com/in/alice/",
            "email": "alice@example.com",
            "state": DealState.READY_TO_EMAIL.value,
            "disqualified": True,
            "updated_at": _json_value(included_lead.update_date),
        },
    ]


def test_lead_list_missing_campaign_returns_not_found_error():
    payload = _oo("lead", "list", "--campaign", "Missing Campaign", "--json")

    assert payload["ok"] is False
    assert payload["command"] == "lead list"
    assert payload["error"]["type"] == "not_found"
    assert payload["result"] is None


def test_lead_list_invalid_state_returns_invalid_argument_error():
    Campaign.objects.create(name="Filtered Campaign")

    payload = _oo(
        "lead",
        "list",
        "--campaign",
        "Filtered Campaign",
        "--state",
        "Typo State",
        "--json",
    )

    assert payload["ok"] is False
    assert payload["command"] == "lead list"
    assert payload["error"]["type"] == "invalid_argument"
    assert payload["result"] is None


def test_lead_list_empty_state_returns_invalid_argument_error():
    Campaign.objects.create(name="Filtered Campaign")

    payload = _oo(
        "lead",
        "list",
        "--campaign",
        "Filtered Campaign",
        "--state",
        "",
        "--json",
    )

    assert payload["ok"] is False
    assert payload["command"] == "lead list"
    assert payload["error"]["type"] == "invalid_argument"
    assert payload["result"] is None


def test_task_list_returns_recent_tasks():
    older = Task.objects.create(
        task_type=Task.TaskType.FIND_EMAIL,
        status=Task.Status.COMPLETED,
        scheduled_at=timezone.now(),
        payload={"campaign_id": 1},
    )
    newer = Task.objects.create(
        task_type=Task.TaskType.FOLLOW_UP,
        status=Task.Status.PENDING,
        scheduled_at=timezone.now(),
        payload={"deal_id": 7},
    )

    payload = _oo("task", "list", "--json")

    assert payload["ok"] is True
    assert payload["command"] == "task list"
    assert payload["result"]["tasks"] == [
        {
            "id": newer.id,
            "type": Task.TaskType.FOLLOW_UP.value,
            "status": Task.Status.PENDING.value,
            "scheduled_at": _json_value(newer.scheduled_at),
            "payload": {"deal_id": 7},
            "created_at": _json_value(newer.created_at),
        },
        {
            "id": older.id,
            "type": Task.TaskType.FIND_EMAIL.value,
            "status": Task.Status.COMPLETED.value,
            "scheduled_at": _json_value(older.scheduled_at),
            "payload": {"campaign_id": 1},
            "created_at": _json_value(older.created_at),
        },
    ]


def _action_log(action_type: str, **kwargs) -> ActionLog:
    return ActionLog.objects.create(
        action_type=action_type,
        target_type=kwargs.pop("target_type", "campaign"),
        target_id=kwargs.pop("target_id", "1"),
        payload_hash=kwargs.pop("payload_hash", action_type.replace(".", "")),
        idempotency_key=kwargs.pop("idempotency_key", f"{action_type}-key"),
        status=kwargs.pop("status", ActionLog.Status.PLANNED),
        result=kwargs.pop("result", None),
        error_type=kwargs.pop("error_type", ""),
        error_message=kwargs.pop("error_message", ""),
        **kwargs,
    )


def test_audit_list_returns_default_recent_action_logs():
    actions = [
        _action_log(
            f"audit.default.{index}",
            target_id=str(index),
            idempotency_key=f"audit-default-{index}",
            result={"index": index},
        )
        for index in range(51)
    ]

    payload = _oo("audit", "list", "--json")

    assert payload["ok"] is True
    assert payload["command"] == "audit list"
    assert payload["result"]["limit"] == 50
    assert len(payload["result"]["actions"]) == 50
    assert [action["id"] for action in payload["result"]["actions"]] == [
        action.id for action in reversed(actions[1:])
    ]
    assert payload["result"]["actions"][0] == {
        "id": actions[-1].id,
        "action_type": "audit.default.50",
        "target_type": "campaign",
        "target_id": "50",
        "idempotency_key": "audit-default-50",
        "status": ActionLog.Status.PLANNED.value,
        "result": {"index": 50},
        "error_type": "",
        "error_message": "",
        "created_at": _json_value(actions[-1].created_at),
        "updated_at": _json_value(actions[-1].updated_at),
    }


def test_audit_list_honors_explicit_limit_and_id_desc_tie_breaker():
    first = _action_log("audit.limit.first", idempotency_key="audit-limit-first")
    second = _action_log("audit.limit.second", idempotency_key="audit-limit-second")
    created_at = timezone.now()
    ActionLog.objects.filter(pk__in=[first.pk, second.pk]).update(
        created_at=created_at,
        updated_at=created_at,
    )

    payload = _oo("audit", "list", "--limit", "1", "--json")

    assert payload["ok"] is True
    assert payload["command"] == "audit list"
    assert payload["result"]["limit"] == 1
    assert [action["id"] for action in payload["result"]["actions"]] == [second.id]


@pytest.mark.parametrize("limit", ["", "0", "201", "not-a-number"])
def test_audit_list_invalid_limit_returns_invalid_argument(limit):
    args = (
        ("audit", "list", "--limit", limit, "--json")
        if limit
        else ("audit", "list", "--limit", "--json")
    )

    payload = _oo(*args)

    assert payload["ok"] is False
    assert payload["command"] == "audit list"
    assert payload["error"]["type"] == "invalid_argument"
    assert payload["result"] is None


def test_email_send_next_dry_run_plans_action_without_sending():
    campaign, _user = _campaign_with_operator()
    _box()
    _ready(campaign)

    summary, compose, send = _send_mocks()
    with summary, compose as compose_mock, send as send_mock:
        payload = _oo(
            "email",
            "send-next",
            "--campaign",
            campaign.name,
            "--dry-run",
            "--idempotency-key",
            "dry-run-1",
            "--json",
        )

    action = ActionLog.objects.get(action_type="email.send_next", idempotency_key="dry-run-1")
    assert payload["ok"] is True
    assert payload["command"] == "email send-next"
    assert payload["status"] == ActionLog.Status.PLANNED.value
    assert payload["dry_run"] is True
    assert payload["action_id"] == action.pk
    assert payload["result"] == {"planned": True}
    assert action.status == ActionLog.Status.PLANNED
    compose_mock.assert_not_called()
    send_mock.assert_not_called()


def test_email_send_next_non_interactive_sends_and_records_result():
    campaign, _user = _campaign_with_operator()
    box = _box()
    deal = _ready(campaign, "lead@example.com")

    summary, _compose, send = _send_mocks()
    with summary, _compose, send as send_mock:
        payload = _oo(
            "email",
            "send-next",
            "--campaign",
            campaign.name,
            "--non-interactive",
            "--idempotency-key",
            "send-1",
            "--json",
        )

    deal.refresh_from_db()
    action = ActionLog.objects.get(action_type="email.send_next", idempotency_key="send-1")
    assert payload["ok"] is True
    assert payload["status"] == ActionLog.Status.SUCCEEDED.value
    assert payload["action_id"] == action.pk
    assert payload["result"] == {
        "campaign": campaign.name,
        "deal_id": deal.id,
        "lead_id": deal.lead_id,
        "profile_url": deal.lead.profile_url,
        "email": "lead@example.com",
        "mailbox": "sender@example.com",
        "message_id": "<mid@example.com>",
        "subject": "Hi there",
    }
    send_mock.assert_called_once_with(
        box,
        "lead@example.com",
        "Hi there",
        "Short opener.",
        bcc="operator@example.com",
    )
    assert action.status == ActionLog.Status.SUCCEEDED
    assert action.result == payload["result"]
    assert deal.state == DealState.EMAILED
    assert deal.mailbox == box
    assert deal.email_message_id == "<mid@example.com>"


def test_email_send_next_duplicate_idempotency_key_does_not_send_twice():
    campaign, _user = _campaign_with_operator()
    _box()
    _ready(campaign, "lead@example.com")

    summary, _compose, send = _send_mocks()
    with summary, _compose, send as send_mock:
        first = _oo(
            "email",
            "send-next",
            "--campaign",
            campaign.name,
            "--confirm",
            "--idempotency-key",
            "dup-send-1",
            "--json",
        )
        second = _oo(
            "email",
            "send-next",
            "--campaign",
            campaign.name,
            "--confirm",
            "--idempotency-key",
            "dup-send-1",
            "--json",
        )

    assert first["ok"] is True
    assert second["ok"] is True
    assert second["action_id"] == first["action_id"]
    assert second["result"] == {
        "duplicate": True,
        "original_action_id": first["action_id"],
    }
    send_mock.assert_called_once()


def test_email_send_next_no_eligible_email_returns_error_and_failed_action():
    campaign, _user = _campaign_with_operator()
    _box()

    payload = _oo(
        "email",
        "send-next",
        "--campaign",
        campaign.name,
        "--non-interactive",
        "--idempotency-key",
        "no-eligible-1",
        "--json",
    )

    action = ActionLog.objects.get(
        action_type="email.send_next",
        idempotency_key="no-eligible-1",
    )
    assert payload["ok"] is False
    assert payload["error"]["type"] == "no_eligible_email"
    assert payload["action_id"] == action.pk
    assert action.status == ActionLog.Status.FAILED
    assert action.error_type == "NoEligibleEmail"


def test_email_send_next_duplicate_no_eligible_error_stays_normalized():
    campaign, _user = _campaign_with_operator()
    _box()

    first = _oo(
        "email",
        "send-next",
        "--campaign",
        campaign.name,
        "--non-interactive",
        "--idempotency-key",
        "no-eligible-dup-1",
        "--json",
    )
    second = _oo(
        "email",
        "send-next",
        "--campaign",
        campaign.name,
        "--non-interactive",
        "--idempotency-key",
        "no-eligible-dup-1",
        "--json",
    )

    assert first["ok"] is False
    assert first["error"]["type"] == "no_eligible_email"
    assert second["ok"] is False
    assert second["error"]["type"] == "no_eligible_email"
    assert second["action_id"] == first["action_id"]


def test_email_send_next_send_failure_returns_json_and_failed_action():
    campaign, _user = _campaign_with_operator()
    _box()
    deal = _ready(campaign, "lead@example.com")

    summary, _compose, send = _send_mocks()
    with summary, _compose, send as send_mock:
        send_mock.side_effect = RuntimeError("smtp down")
        payload = _oo(
            "email",
            "send-next",
            "--campaign",
            campaign.name,
            "--non-interactive",
            "--idempotency-key",
            "send-fails-1",
            "--json",
        )

    action = ActionLog.objects.get(action_type="email.send_next", idempotency_key="send-fails-1")
    deal.refresh_from_db()
    assert payload["ok"] is False
    assert payload["error"]["type"] == "RuntimeError"
    assert payload["error"]["message"] == "smtp down"
    assert payload["action_id"] == action.pk
    assert action.status == ActionLog.Status.FAILED
    assert action.error_type == "RuntimeError"
    assert deal.state == DealState.READY_TO_EMAIL


def test_email_send_next_claim_prevents_nested_send_with_different_key():
    campaign, _user = _campaign_with_operator()
    _box()
    _ready(campaign, "lead@example.com")
    nested_payloads = []

    def _send_once_then_try_nested(*args, **kwargs):
        nested_payloads.append(
            _oo(
                "email",
                "send-next",
                "--campaign",
                campaign.name,
                "--non-interactive",
                "--idempotency-key",
                "nested-send-2",
                "--json",
            ),
        )
        return "<mid@example.com>"

    summary, _compose, send = _send_mocks()
    with summary, _compose, send as send_mock:
        send_mock.side_effect = _send_once_then_try_nested
        first = _oo(
            "email",
            "send-next",
            "--campaign",
            campaign.name,
            "--non-interactive",
            "--idempotency-key",
            "nested-send-1",
            "--json",
        )

    assert first["ok"] is True
    assert nested_payloads == [
        {
            "ok": False,
            "command": "email send-next",
            "status": "failed",
            "dry_run": False,
            "action_id": ActionLog.objects.get(idempotency_key="nested-send-2").pk,
            "result": None,
            "error": {
                "type": "no_eligible_email",
                "message": "No mailbox under cap or READY_TO_EMAIL deal available.",
            },
            "warnings": [],
        },
    ]
    send_mock.assert_called_once()


def test_email_send_next_missing_campaign_returns_not_found():
    payload = _oo(
        "email",
        "send-next",
        "--campaign",
        "Missing Campaign",
        "--dry-run",
        "--idempotency-key",
        "missing-campaign-1",
        "--json",
    )

    assert payload["ok"] is False
    assert payload["error"]["type"] == "not_found"


@pytest.mark.parametrize(
    "campaign_args",
    [
        (),
        ("--campaign", ""),
    ],
)
def test_email_send_next_missing_or_blank_campaign_returns_invalid_argument(campaign_args):
    payload = _oo(
        "email",
        "send-next",
        *campaign_args,
        "--dry-run",
        "--idempotency-key",
        "missing-campaign-input-1",
        "--json",
    )

    assert payload["ok"] is False
    assert payload["error"]["type"] == "invalid_argument"


@pytest.mark.parametrize(
    "args",
    [
        ("--dry-run",),
        ("--dry-run", "--idempotency-key", ""),
    ],
)
def test_email_send_next_missing_or_blank_idempotency_key_returns_invalid_argument(args):
    campaign, _user = _campaign_with_operator()

    payload = _oo(
        "email",
        "send-next",
        "--campaign",
        campaign.name,
        *args,
        "--json",
    )

    assert payload["ok"] is False
    assert payload["error"]["type"] == "invalid_argument"


def test_email_send_next_invalid_mode_returns_invalid_argument():
    campaign, _user = _campaign_with_operator()

    payload = _oo(
        "email",
        "send-next",
        "--campaign",
        campaign.name,
        "--dry-run",
        "--confirm",
        "--idempotency-key",
        "bad-mode-1",
        "--json",
    )

    assert payload["ok"] is False
    assert payload["error"]["type"] == "invalid_argument"
