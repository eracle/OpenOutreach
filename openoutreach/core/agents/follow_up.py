# openoutreach/core/agents/follow_up.py
"""Follow-up agent: reads the email thread, returns a structured decision.

Single LLM call with structured output — no tool-calling loop.
The handler in ``emails/tasks/follow_up.py`` executes the decision.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_ai import Agent

from openoutreach.core.agents.prompt import _format_facts
from openoutreach.core.llm import get_llm_model, run_agent_sync

logger = logging.getLogger(__name__)


class FollowUpDecision(BaseModel):
    """Structured output from the follow-up agent."""

    action: Literal["send_message", "mark_completed", "wait"] = Field(
        description="What to do next for this lead.",
    )
    message: str | None = Field(
        default=None,
        description="The message to send. Required when action='send_message'.",
    )
    outcome: Literal[
        "converted", "not_interested", "wrong_fit", "no_budget",
        "has_solution", "bad_timing", "unresponsive",
    ] | None = Field(
        default=None,
        description="Why the conversation ended. Required when action='mark_completed'.",
    )
    follow_up_hours: float = Field(
        description="Hours until next follow-up. Always required — you decide the pace.",
    )

    @model_validator(mode="after")
    def _check_required_fields(self):
        if self.action == "send_message" and not self.message:
            raise ValueError("message is required when action='send_message'")
        if self.action == "mark_completed" and not self.outcome:
            raise ValueError("outcome is required when action='mark_completed'")
        return self


# Number of trailing verbatim messages the agent sees alongside the rolling
# chat_summary. Older turns live in the summary fact list; the recency window
# preserves literal phrasing for the turns that matter most when composing
# the next reply.
RECENT_MESSAGES_WINDOW = 6


def _humanize_age(when: datetime, now: datetime) -> str:
    """Render `when` as a coarse age relative to `now` (e.g. ``3d ago``)."""
    delta = now - when
    if delta < timedelta(hours=1):
        return f"{max(int(delta.total_seconds() // 60), 1)}m ago"
    if delta < timedelta(days=1):
        return f"{int(delta.total_seconds() // 3600)}h ago"
    return f"{delta.days}d ago"


def _format_recent_messages(messages: list, now: datetime) -> str:
    """Render the last few ChatMessage rows as a timestamped transcript."""
    if not messages:
        return "No recent messages."
    lines = []
    for m in messages:
        content = (m.content or "").strip()
        if not content:
            continue
        speaker = "Me" if m.is_outgoing else "Lead"
        prefix = f"{speaker} ({_humanize_age(m.creation_date, now)})" if m.creation_date else speaker
        lines.append(f"{prefix}: {content}")
    return "\n".join(lines) or "No recent messages."


def _days_since_last_outgoing(messages: list, now: datetime) -> int | None:
    """Whole days since the most recent outgoing message, or None if there are none."""
    timestamps = [m.creation_date for m in messages if m.is_outgoing and m.creation_date]
    if not timestamps:
        return None
    return max((now - max(timestamps)).days, 0)


def _count_unanswered_outgoing(messages: list) -> int:
    """Trailing run of outgoing messages with no lead reply after them."""
    count = 0
    for m in reversed(messages):
        if m.is_outgoing:
            count += 1
        else:
            break
    return count


def _log_chat_facts(public_id: str, deal) -> None:
    """Log the mem0 chat facts the agent is working with."""
    chat_facts = (deal.chat_summary or {}).get("facts", [])
    if not chat_facts:
        return
    lines = [f"chat facts for {public_id}:"]
    lines.extend(f"  • {f}" for f in chat_facts)
    logger.info("\n".join(lines))


def _load_recent_messages(deal, limit: int = RECENT_MESSAGES_WINDOW) -> list:
    """Last `limit` ChatMessages for `deal`, in chronological order.

    The recency window of verbatim turns the agent sees alongside the rolling
    ``chat_summary`` — the opener plus any replies read from the mailbox.
    """
    from openoutreach.chat.models import ChatMessage

    qs = ChatMessage.objects.filter(deal=deal).order_by("-creation_date", "-pk")[:limit]
    return list(reversed(list(qs)))


def _render_system_prompt(session, deal, recent_messages: list) -> str:
    """Render the email follow-up prompt: shared base + the in-thread extras."""
    from django.utils import timezone
    from openoutreach.core.agents.prompt import base_context, render

    now = timezone.now()
    return render(
        "follow_up_agent.j2",
        **base_context(session, deal),
        chat_summary=_format_facts(deal.chat_summary),
        recent_messages=_format_recent_messages(recent_messages, now),
        today=now.strftime("%Y-%m-%d"),
        days_since_last_outgoing=_days_since_last_outgoing(recent_messages, now),
        unanswered_outgoing=_count_unanswered_outgoing(recent_messages),
    )


def run_follow_up_agent(session, deal) -> FollowUpDecision:
    """Read the email thread and return a structured follow-up decision.

    Reads replies first (folding new inbound messages into ``deal.chat_summary``),
    then renders the prompt from the Deal's persistent summaries plus a small
    recency window of verbatim messages, and asks the LLM to decide. The opener is
    the separate first-touch path — see ``core.agents.email_opener``.
    """
    public_id = deal.lead.public_identifier
    from openoutreach.emails.inbox import sync_inbox

    sync_inbox(session, deal)
    deal.refresh_from_db(fields=["chat_summary", "profile_summary"])
    _log_chat_facts(public_id, deal)

    recent = _load_recent_messages(deal)
    system_prompt = _render_system_prompt(session, deal, recent)

    agent = Agent(
        get_llm_model(),
        output_type=FollowUpDecision,
        model_settings={"temperature": 0.7, "timeout": 60},
    )
    decision = run_agent_sync(agent.run(system_prompt)).output
    if decision is None:
        raise RuntimeError(f"LLM returned unparseable response for follow-up of {public_id}")

    logger.info("follow_up agent for %s: %s", public_id, decision.action)
    return decision


if __name__ == "__main__":
    from openoutreach.crm.models import Deal
    from openoutreach.linkedin.browser.registry import cli_parser, cli_session
    from openoutreach.core.db.summaries import materialize_profile_summary_if_missing
    from openoutreach.core.models import Task

    parser = cli_parser("Run the follow-up agent for a profile")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--profile", help="Public identifier of the target profile")
    group.add_argument("--task-id", type=int, help="Task ID to run the agent for")
    args = parser.parse_args()
    session = cli_session(args)
    session.ensure_browser()

    if args.task_id:
        task = Task.objects.get(pk=args.task_id)
        public_id = task.payload["public_id"]
        campaign_id = task.payload["campaign_id"]
        from openoutreach.core.models import Campaign
        campaign = Campaign.objects.get(pk=campaign_id)
        session.campaign = campaign
    else:
        public_id = args.profile

    deal = (
        Deal.objects.filter(lead__public_identifier=public_id, campaign=session.campaign)
        .select_related("lead", "campaign")
        .first()
    )
    if not deal:
        logger.error("No Deal found for %s", public_id)
        raise SystemExit(1)

    logger.info("Running follow-up agent as %s for %s", session, public_id)
    logger.info("Campaign: %s", session.campaign)

    materialize_profile_summary_if_missing(deal, session)
    decision = run_follow_up_agent(session, deal)

    logger.info("Chat facts: %s", _format_facts(deal.chat_summary))
    logger.info("Action: %s", decision.action)
    if decision.message:
        logger.info("Message: %s", decision.message)
    if decision.outcome:
        logger.info("Outcome: %s", decision.outcome)
    logger.info("Follow-up in: %sh", decision.follow_up_hours)
