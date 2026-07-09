from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Count

from openoutreach.core.agent_contract import error_response, success_response


DEFAULT_LIMIT = 50


class Command(BaseCommand):
    help = "Read-only OpenOutreach operator controls."

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="resource", required=True)

        status = subparsers.add_parser("status")
        self._add_json_flag(status)
        status.set_defaults(handler=self._status, command_name="status")

        campaign = subparsers.add_parser("campaign")
        campaign_subparsers = campaign.add_subparsers(dest="campaign_command", required=True)
        campaign_list = campaign_subparsers.add_parser("list")
        self._add_json_flag(campaign_list)
        campaign_list.set_defaults(handler=self._campaign_list, command_name="campaign list")

        lead = subparsers.add_parser("lead")
        lead_subparsers = lead.add_subparsers(dest="lead_command", required=True)
        lead_list = lead_subparsers.add_parser("list")
        lead_list.add_argument("--campaign", required=True)
        lead_list.add_argument("--state")
        self._add_json_flag(lead_list)
        lead_list.set_defaults(handler=self._lead_list, command_name="lead list")

        task = subparsers.add_parser("task")
        task_subparsers = task.add_subparsers(dest="task_command", required=True)
        task_list = task_subparsers.add_parser("list")
        self._add_json_flag(task_list)
        task_list.set_defaults(handler=self._task_list, command_name="task list")

    def handle(self, *args, **options):
        response = options["handler"](options)
        self.stdout.write(json.dumps(response, cls=DjangoJSONEncoder))

    def _add_json_flag(self, parser) -> None:
        parser.add_argument(
            "--json",
            action="store_true",
            required=True,
            help="Emit the agent-control JSON envelope.",
        )

    def _status(self, options: dict[str, Any]) -> dict[str, Any]:
        from openoutreach.core.models import Campaign, Task
        from openoutreach.crm.models import Deal, Lead

        return success_response(
            command=options["command_name"],
            result={
                "campaigns": Campaign.objects.count(),
                "leads": Lead.objects.count(),
                "deals": Deal.objects.count(),
                "pending_tasks": Task.objects.filter(status=Task.Status.PENDING).count(),
                "running_tasks": Task.objects.filter(status=Task.Status.RUNNING).count(),
                "failed_tasks": Task.objects.filter(status=Task.Status.FAILED).count(),
            },
        )

    def _campaign_list(self, options: dict[str, Any]) -> dict[str, Any]:
        from openoutreach.core.models import Campaign

        campaigns = (
            Campaign.objects.annotate(
                users_count=Count("users", distinct=True),
                deal_count=Count("deals", distinct=True),
            )
            .order_by("name", "id")[:DEFAULT_LIMIT]
        )

        return success_response(
            command=options["command_name"],
            result={
                "campaigns": [
                    {
                        "id": campaign.id,
                        "name": campaign.name,
                        "users_count": campaign.users_count,
                        "deal_count": campaign.deal_count,
                        "discovery_offset": campaign.discovery_offset,
                    }
                    for campaign in campaigns
                ],
                "limit": DEFAULT_LIMIT,
            },
        )

    def _lead_list(self, options: dict[str, Any]) -> dict[str, Any]:
        from openoutreach.core.models import Campaign
        from openoutreach.crm.models import Deal, DealState

        campaign_name = options["campaign"]
        try:
            campaign = Campaign.objects.get(name=campaign_name)
        except Campaign.DoesNotExist:
            return error_response(
                command=options["command_name"],
                error_type="not_found",
                message=f"Campaign not found: {campaign_name}",
            )

        state = options.get("state")
        if state and state not in DealState.values:
            return error_response(
                command=options["command_name"],
                error_type="invalid_argument",
                message=f"Invalid lead state: {state}",
            )

        deals = Deal.objects.filter(campaign=campaign).select_related("lead")
        if state:
            deals = deals.filter(state=state)

        deals = deals.order_by("lead__profile_url", "id")[:DEFAULT_LIMIT]

        return success_response(
            command=options["command_name"],
            result={
                "campaign": campaign.name,
                "state": options.get("state"),
                "leads": [
                    {
                        "deal_id": deal.id,
                        "lead_id": deal.lead_id,
                        "profile_url": deal.lead.profile_url,
                        "email": deal.lead.email,
                        "state": deal.state,
                        "disqualified": deal.lead.disqualified,
                        "updated_at": deal.lead.update_date,
                    }
                    for deal in deals
                ],
                "limit": DEFAULT_LIMIT,
            },
        )

    def _task_list(self, options: dict[str, Any]) -> dict[str, Any]:
        from openoutreach.core.models import Task

        tasks = Task.objects.order_by("-created_at", "-id")[:DEFAULT_LIMIT]

        return success_response(
            command=options["command_name"],
            result={
                "tasks": [
                    {
                        "id": task.id,
                        "type": task.task_type,
                        "status": task.status,
                        "scheduled_at": task.scheduled_at,
                        "payload": task.payload,
                        "created_at": task.created_at,
                    }
                    for task in tasks
                ],
                "limit": DEFAULT_LIMIT,
            },
        )
