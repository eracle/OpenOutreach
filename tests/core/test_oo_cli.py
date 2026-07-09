from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from openoutreach.core.models import Campaign, Task
from openoutreach.crm.models import DealState
from tests.factories import DealFactory, LeadFactory, UserFactory

pytestmark = pytest.mark.django_db


def _oo(*args: str) -> dict:
    stdout = StringIO()
    call_command("oo", *args, stdout=stdout)
    return json.loads(stdout.getvalue())


def _json_value(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


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
