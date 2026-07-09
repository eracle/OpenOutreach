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

        email = subparsers.add_parser("email")
        email_subparsers = email.add_subparsers(dest="email_command", required=True)
        email_send_next = email_subparsers.add_parser("send-next")
        email_send_next.add_argument("--campaign")
        email_send_next.add_argument("--dry-run", action="store_true")
        email_send_next.add_argument("--confirm", action="store_true")
        email_send_next.add_argument("--non-interactive", action="store_true")
        email_send_next.add_argument("--idempotency-key")
        self._add_json_flag(email_send_next)
        email_send_next.set_defaults(
            handler=self._email_send_next,
            command_name="email send-next",
        )

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
        if state is not None and state not in DealState.values:
            return error_response(
                command=options["command_name"],
                error_type="invalid_argument",
                message=f"Invalid lead state: {state}",
            )

        deals = Deal.objects.filter(campaign=campaign).select_related("lead")
        if state is not None:
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

    def _email_send_next(self, options: dict[str, Any]) -> dict[str, Any]:
        from openoutreach.core.actions import run_logged_action
        from openoutreach.core.models import ActionLog, Campaign
        from openoutreach.emails.tasks.send import NoEligibleEmail, send_next_email

        dry_run = bool(options.get("dry_run"))
        confirm = bool(options.get("confirm"))
        non_interactive = bool(options.get("non_interactive"))
        if sum([dry_run, confirm, non_interactive]) != 1:
            return error_response(
                command=options["command_name"],
                error_type="invalid_argument",
                message="Specify exactly one of --dry-run, --confirm, or --non-interactive.",
            )

        idempotency_key = options.get("idempotency_key")
        if not idempotency_key or not idempotency_key.strip():
            return error_response(
                command=options["command_name"],
                error_type="invalid_argument",
                message="--idempotency-key is required and cannot be blank.",
                dry_run=dry_run,
            )

        campaign_name = options.get("campaign")
        if not campaign_name or not campaign_name.strip():
            return error_response(
                command=options["command_name"],
                error_type="invalid_argument",
                message="--campaign is required and cannot be blank.",
                dry_run=dry_run,
            )

        try:
            campaign = Campaign.objects.get(name=campaign_name)
        except Campaign.DoesNotExist:
            return error_response(
                command=options["command_name"],
                error_type="not_found",
                message=f"Campaign not found: {campaign_name}",
                dry_run=dry_run,
            )

        session = self._session_for_campaign(campaign)
        if session is None:
            return error_response(
                command=options["command_name"],
                error_type="invalid_argument",
                message=f"No active operator account found for campaign: {campaign.name}",
                dry_run=dry_run,
            )

        payload = {
            "campaign_id": campaign.pk,
            "campaign": campaign.name,
        }
        action_type = "email.send_next"

        try:
            action, result = run_logged_action(
                action_type=action_type,
                target_type="campaign",
                target_id=str(campaign.pk),
                payload=payload,
                idempotency_key=idempotency_key,
                dry_run=dry_run,
                execute=lambda: send_next_email(session),
            )
        except NoEligibleEmail as exc:
            action = ActionLog.objects.filter(
                action_type=action_type,
                idempotency_key=idempotency_key,
            ).first()
            return error_response(
                command=options["command_name"],
                error_type="no_eligible_email",
                message=str(exc),
                dry_run=dry_run,
                action_id=action.pk if action else None,
            )
        except ValueError as exc:
            return error_response(
                command=options["command_name"],
                error_type="invalid_argument",
                message=str(exc),
                dry_run=dry_run,
            )
        except Exception as exc:
            action = ActionLog.objects.filter(
                action_type=action_type,
                idempotency_key=idempotency_key,
            ).first()
            return error_response(
                command=options["command_name"],
                error_type=self._action_error_type(action, exc),
                message=action.error_message if action else str(exc),
                dry_run=dry_run,
                action_id=action.pk if action else None,
            )

        if action.status == ActionLog.Status.FAILED:
            return error_response(
                command=options["command_name"],
                error_type=self._action_error_type(action),
                message=action.error_message or "Action failed.",
                dry_run=dry_run,
                action_id=action.pk,
            )

        return success_response(
            command=options["command_name"],
            status=action.status,
            dry_run=dry_run,
            action_id=action.pk,
            result=result,
        )

    def _session_for_campaign(self, campaign):
        from openoutreach.core.session import OperatorSession

        user = (
            campaign.users.filter(is_active=True, is_staff=True).order_by("pk").first()
            or campaign.users.filter(is_active=True).order_by("pk").first()
        )
        if user is None:
            return None

        session = OperatorSession(user)
        session.campaign = campaign
        return session

    def _action_error_type(self, action, exc: Exception | None = None) -> str:
        error_type = action.error_type if action else ""
        if error_type == "NoEligibleEmail":
            return "no_eligible_email"
        if error_type:
            return error_type
        return exc.__class__.__name__ if exc else "failed"
