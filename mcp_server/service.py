"""MCP tool service layer for OpenOutreach."""
from __future__ import annotations

import json
from datetime import timedelta
from dataclasses import dataclass
from typing import Any, Callable

from django.db.models import Q
from django.utils import timezone

from linkedin.conf import CAMPAIGN_CONFIG, get_first_active_profile_handle
from linkedin.db.crm_profiles import (
    count_leads_for_qualification,
    count_qualified_profiles,
    get_profile,
    pipeline_needs_refill,
    set_profile_state,
    url_to_public_id,
)
from linkedin.ml.embeddings import count_labeled, get_qualification_reason
from linkedin.navigation.enums import ProfileState
from linkedin.sessions.registry import get_session


DEAL_STATES = {
    ProfileState.NEW.value,
    ProfileState.PENDING.value,
    ProfileState.CONNECTED.value,
    ProfileState.COMPLETED.value,
    ProfileState.FAILED.value,
}

STAGE_NAME_BY_STATE = {
    ProfileState.NEW.value: "New",
    ProfileState.PENDING.value: "Pending",
    ProfileState.CONNECTED.value: "Connected",
    ProfileState.COMPLETED.value: "Completed",
    ProfileState.FAILED.value: "Failed",
}

LISTABLE_STATES = {
    "url_only",
    "enriched",
    "disqualified",
    *DEAL_STATES,
}

ALLOWED_TRANSITIONS = {
    ProfileState.NEW.value: {
        ProfileState.NEW.value,
        ProfileState.PENDING.value,
        ProfileState.CONNECTED.value,
        ProfileState.FAILED.value,
    },
    ProfileState.PENDING.value: {
        ProfileState.PENDING.value,
        ProfileState.CONNECTED.value,
        ProfileState.FAILED.value,
    },
    ProfileState.CONNECTED.value: {
        ProfileState.CONNECTED.value,
        ProfileState.COMPLETED.value,
        ProfileState.FAILED.value,
    },
    ProfileState.COMPLETED.value: {ProfileState.COMPLETED.value},
    ProfileState.FAILED.value: {ProfileState.FAILED.value},
}


@dataclass
class ToolError(Exception):
    message: str
    code: str = "tool_error"
    data: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = {"code": self.code, "message": self.message}
        if self.data:
            payload["data"] = self.data
        return payload


def _resolve_session(handle: str | None, campaign: str | None):
    if not handle:
        handle = get_first_active_profile_handle()
    if not handle:
        raise ToolError("No active LinkedIn profile found.", code="no_active_profile")

    session = get_session(handle=handle)
    campaigns = session.campaigns
    if campaign is None:
        selected = campaigns.filter(is_partner=False).first() or campaigns.first()
    elif isinstance(campaign, int):
        selected = campaigns.filter(pk=campaign).first()
    elif isinstance(campaign, str) and campaign.isdigit():
        selected = campaigns.filter(pk=int(campaign)).first()
    else:
        selected = campaigns.filter(department__name=campaign).first()

    if selected is None:
        raise ToolError(
            "Campaign not found for this profile.",
            code="campaign_not_found",
            data={"handle": handle, "campaign": campaign},
        )

    session.campaign = selected
    return session


def _parse_limit(raw: Any, default: int = 50, max_value: int = 500) -> int:
    if raw is None:
        return default
    if isinstance(raw, bool):
        raise ToolError("Invalid limit value.", code="invalid_argument", data={"limit": raw})
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ToolError("Invalid limit value.", code="invalid_argument", data={"limit": raw}) from exc
    if value < 1 or value > max_value:
        raise ToolError(
            f"Limit must be between 1 and {max_value}.",
            code="invalid_argument",
            data={"limit": raw},
        )
    return value


def _lead_to_summary(lead) -> dict[str, Any]:
    website = lead.website
    return {
        "public_identifier": url_to_public_id(website) if website else None,
        "url": website,
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "title": lead.title,
        "updated_at": lead.update_date.isoformat() if getattr(lead, "update_date", None) else None,
    }


def _require_public_identifier(args: dict[str, Any]) -> str:
    public_identifier = args.get("public_identifier")
    if not public_identifier:
        raise ToolError(
            "public_identifier is required.",
            code="invalid_argument",
            data={"field": "public_identifier"},
        )
    return str(public_identifier)


def _deal_counts(session) -> dict[str, int]:
    from crm.models import Deal

    base = Deal.objects.filter(
        owner=session.django_user,
        department=session.campaign.department,
    )
    return {
        state: base.filter(stage__name=stage_name).count()
        for state, stage_name in STAGE_NAME_BY_STATE.items()
    }


def _pending_ready_count(session, recheck_after_hours: float) -> int:
    """Count pending deals ready to be checked without loading full profile payloads."""
    from crm.models import Deal

    now = timezone.now()
    pending_stage = STAGE_NAME_BY_STATE[ProfileState.PENDING.value]
    rows = Deal.objects.filter(
        owner=session.django_user,
        department=session.campaign.department,
        stage__name=pending_stage,
    ).values_list("next_step", "update_date")

    ready = 0
    for next_step, update_date in rows:
        backoff_hours = recheck_after_hours
        if next_step:
            try:
                metadata = json.loads(next_step)
                backoff_hours = float(metadata.get("backoff_hours", recheck_after_hours))
            except (TypeError, ValueError, json.JSONDecodeError):
                backoff_hours = recheck_after_hours
        cutoff = update_date + timedelta(hours=backoff_hours)
        if now >= cutoff:
            ready += 1
    return ready


def _ensure_allowed_transition(current_state: str, new_state: str) -> None:
    allowed = ALLOWED_TRANSITIONS.get(current_state)
    if not allowed or new_state not in allowed:
        raise ToolError(
            "Invalid state transition.",
            code="invalid_transition",
            data={"from": current_state, "to": new_state},
        )


def get_pipeline_stats_tool(args: dict[str, Any]) -> dict[str, Any]:
    from crm.models import Lead

    session = _resolve_session(args.get("handle"), args.get("campaign"))
    recheck_hours = CAMPAIGN_CONFIG["check_pending_recheck_after_hours"]

    predeal_base = Lead.objects.filter(
        owner=session.django_user,
        department=session.campaign.department,
        contact__isnull=True,
    )

    url_only_count = predeal_base.filter(disqualified=False).filter(
        Q(description__isnull=True) | Q(description="")
    ).count()
    enriched_count = predeal_base.filter(disqualified=False).exclude(
        Q(description__isnull=True) | Q(description="")
    ).count()
    disqualified_count = predeal_base.filter(disqualified=True).count()

    deal_counts = _deal_counts(session)

    return {
        "handle": session.handle,
        "campaign": {"id": session.campaign.pk, "name": session.campaign.department.name},
        "pipeline_needs_refill": pipeline_needs_refill(
            session, CAMPAIGN_CONFIG["min_qualifiable_leads"]
        ),
        "qualification_queue_size": count_leads_for_qualification(session),
        "qualified_queue_size": count_qualified_profiles(session),
        "pending_ready_count": _pending_ready_count(session, recheck_hours),
        "connected_ready_count": deal_counts[ProfileState.CONNECTED.value],
        "embeddings_labels": count_labeled(),
        "counts_by_state": {
            "url_only": url_only_count,
            "enriched": enriched_count,
            "disqualified": disqualified_count,
            **deal_counts,
        },
    }


def list_profiles_by_state_tool(args: dict[str, Any]) -> dict[str, Any]:
    from crm.models import Deal, Lead

    session = _resolve_session(args.get("handle"), args.get("campaign"))
    state = str(args.get("state", "")).strip().lower()
    limit = _parse_limit(args.get("limit"), default=50)
    if state not in LISTABLE_STATES:
        raise ToolError(
            "Unknown state.",
            code="invalid_argument",
            data={"state": state, "allowed_states": sorted(LISTABLE_STATES)},
        )

    if state in DEAL_STATES:
        stage_name = STAGE_NAME_BY_STATE[state]
        base_qs = Deal.objects.filter(
            owner=session.django_user,
            department=session.campaign.department,
            stage__name=stage_name,
        )
        total_count = base_qs.count()
        deals = base_qs.select_related("lead").order_by("-update_date")[:limit]
        items = [_lead_to_summary(deal.lead) for deal in deals if deal.lead]
    else:
        base = Lead.objects.filter(
            owner=session.django_user,
            department=session.campaign.department,
            contact__isnull=True,
        )
        if state == "url_only":
            leads = base.filter(disqualified=False).filter(
                Q(description__isnull=True) | Q(description="")
            )
        elif state == "enriched":
            leads = base.filter(disqualified=False).exclude(
                Q(description__isnull=True) | Q(description="")
            )
        else:  # disqualified
            leads = base.filter(disqualified=True)
        total_count = leads.count()
        items = [_lead_to_summary(lead) for lead in leads.order_by("-update_date")[:limit]]

    return {
        "handle": session.handle,
        "campaign": {"id": session.campaign.pk, "name": session.campaign.department.name},
        "state": state,
        "returned_count": len(items),
        "total_count": total_count,
        "items": items,
    }


def get_profile_tool(args: dict[str, Any]) -> dict[str, Any]:
    session = _resolve_session(args.get("handle"), args.get("campaign"))
    public_identifier = _require_public_identifier(args)

    payload = get_profile(session, public_identifier)
    if payload is None:
        raise ToolError("Profile not found.", code="not_found", data={"public_identifier": public_identifier})

    reason = get_qualification_reason(public_identifier)
    return {
        "public_identifier": public_identifier,
        "state": payload.get("state"),
        "profile": payload.get("profile"),
        "qualification_reason": reason,
    }


def get_qualification_reason_tool(args: dict[str, Any]) -> dict[str, Any]:
    public_identifier = _require_public_identifier(args)
    return {
        "public_identifier": public_identifier,
        "qualification_reason": get_qualification_reason(public_identifier),
    }


def render_followup_preview_tool(args: dict[str, Any]) -> dict[str, Any]:
    from linkedin.templates.renderer import render_template

    session = _resolve_session(args.get("handle"), args.get("campaign"))
    public_identifier = _require_public_identifier(args)
    payload = get_profile(session, public_identifier)
    if payload is None:
        raise ToolError("Profile not found.", code="not_found", data={"public_identifier": public_identifier})
    profile = payload.get("profile")
    if not profile:
        raise ToolError(
            "Profile exists but has no enriched data yet.",
            code="profile_not_enriched",
            data={"public_identifier": public_identifier, "state": payload.get("state")},
        )

    template_content = args.get("template_override") or session.campaign.followup_template
    if not template_content:
        raise ToolError("No follow-up template available.", code="template_missing")

    rendered = render_template(session, template_content=template_content, profile=profile)
    return {
        "public_identifier": public_identifier,
        "state": payload.get("state"),
        "message_preview": rendered,
    }


def set_profile_state_tool(args: dict[str, Any]) -> dict[str, Any]:
    session = _resolve_session(args.get("handle"), args.get("campaign"))
    public_identifier = _require_public_identifier(args)
    new_state = str(args.get("new_state", "")).strip().lower()
    reason = str(args.get("reason", "")).strip()
    if new_state not in DEAL_STATES:
        raise ToolError(
            "new_state must be a deal state.",
            code="invalid_argument",
            data={"new_state": new_state, "allowed_states": sorted(DEAL_STATES)},
        )

    current = get_profile(session, public_identifier)
    if current is None:
        raise ToolError("Profile not found.", code="not_found", data={"public_identifier": public_identifier})

    current_state = str(current.get("state"))
    if current_state not in DEAL_STATES:
        raise ToolError(
            "Profile is not in a deal state; transition denied.",
            code="invalid_transition",
            data={"from": current_state, "to": new_state},
        )

    _ensure_allowed_transition(current_state, new_state)
    set_profile_state(session, public_identifier, new_state, reason=reason)

    return {
        "public_identifier": public_identifier,
        "from_state": current_state,
        "to_state": new_state,
        "reason": reason,
    }


TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "get_pipeline_stats": get_pipeline_stats_tool,
    "list_profiles_by_state": list_profiles_by_state_tool,
    "get_profile": get_profile_tool,
    "get_qualification_reason": get_qualification_reason_tool,
    "render_followup_preview": render_followup_preview_tool,
    "set_profile_state": set_profile_state_tool,
}


def run_tool(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        raise ToolError("Unknown tool.", code="unknown_tool", data={"tool_name": name})
    if arguments is None:
        return handler({})
    if not isinstance(arguments, dict):
        raise ToolError(
            "Tool arguments must be a JSON object.",
            code="invalid_argument",
            data={"arguments_type": type(arguments).__name__},
        )
    return handler(arguments)
