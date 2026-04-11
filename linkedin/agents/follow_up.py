# linkedin/agents/follow_up.py
"""Follow-up agent: reads conversation, returns a structured decision.

Single LLM call with structured output — no tool-calling loop.
The handler in tasks/follow_up.py executes the decision.
"""
from __future__ import annotations

import logging
from typing import Literal

import jinja2
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, model_validator

from linkedin.conf import PROMPTS_DIR, get_llm_config

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
    reason: str | None = Field(
        default=None,
        description="Why mark completed. Required when action='mark_completed'.",
    )
    follow_up_hours: float | None = Field(
        default=None,
        description="Hours until next follow-up. Required for 'send_message' and 'wait'. Ignored for 'mark_completed'.",
    )

    @model_validator(mode="after")
    def _check_required_fields(self):
        if self.action == "send_message" and not self.message:
            raise ValueError("message is required when action='send_message'")
        if self.action == "mark_completed" and not self.reason:
            raise ValueError("reason is required when action='mark_completed'")
        if self.action in ("send_message", "wait") and self.follow_up_hours is None:
            self.follow_up_hours = 72
        return self


# Number of trailing verbatim messages the agent sees alongside the rolling
# chat_summary. Older turns live in the summary fact list; the recency window
# preserves literal phrasing for the turns that matter most when composing
# the next reply.
RECENT_MESSAGES_WINDOW = 6


def _format_recent_messages(messages: list) -> str:
    """Render the last few ChatMessage rows as a `Me:`/`Lead:` transcript."""
    if not messages:
        return "No recent messages."
    lines = []
    for m in messages:
        speaker = "Me" if m.is_outgoing else "Lead"
        content = (m.content or "").strip()
        if content:
            lines.append(f"{speaker}: {content}")
    return "\n".join(lines) or "No recent messages."


def _format_facts(summary: dict | None) -> str:
    """Render a `{facts: [...]}` summary blob as a bullet list."""
    facts = (summary or {}).get("facts") or []
    if not facts:
        return "(none yet)"
    return "\n".join(f"- {f}" for f in facts)


def _load_recent_messages(deal, limit: int = RECENT_MESSAGES_WINDOW) -> list:
    """Last `limit` ChatMessages for `deal.lead`, in chronological order."""
    from chat.models import ChatMessage
    from django.contrib.contenttypes.models import ContentType

    ct = ContentType.objects.get_for_model(deal.lead.__class__)
    qs = (
        ChatMessage.objects
        .filter(content_type=ct, object_id=deal.lead_id)
        .order_by("-creation_date", "-pk")[:limit]
    )
    return list(reversed(list(qs)))


def _render_system_prompt(session, deal, recent_messages: list) -> str:
    """Render the agent system prompt from the Jinja2 template."""
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(PROMPTS_DIR)))
    template = env.get_template("follow_up_agent.j2")

    campaign = deal.campaign
    self_prof = session.self_profile
    self_name = f"{self_prof.get('first_name', '')} {self_prof.get('last_name', '')}".strip() or session.django_user.username

    return template.render(
        self_name=self_name,
        product_docs=campaign.product_docs or "",
        campaign_objective=campaign.campaign_objective or "",
        booking_link=campaign.booking_link or "",
        profile_summary=_format_facts(deal.profile_summary),
        chat_summary=_format_facts(deal.chat_summary),
        recent_messages=_format_recent_messages(recent_messages),
    )


def run_follow_up_agent(session, deal) -> FollowUpDecision:
    """Read conversation and return a structured follow-up decision.

    Sync chat first (which folds new messages into ``deal.chat_summary``),
    then render the prompt from the Deal's persistent summaries plus a small
    recency window of verbatim messages, and ask the LLM to decide.
    """
    from linkedin.db.chat import sync_conversation

    public_id = deal.lead.public_identifier
    sync_conversation(session, public_id)
    deal.refresh_from_db(fields=["chat_summary", "profile_summary"])

    recent = _load_recent_messages(deal)
    system_prompt = _render_system_prompt(session, deal, recent)

    llm_api_key, ai_model, llm_api_base = get_llm_config()
    llm = ChatOpenAI(
        model=ai_model,
        temperature=0.7,
        api_key=llm_api_key,
        base_url=llm_api_base,
        timeout=60,
    )
    structured_llm = llm.with_structured_output(FollowUpDecision)
    decision = structured_llm.invoke(system_prompt)
    if decision is None:
        raise RuntimeError(f"LLM returned unparseable response for follow-up of {public_id}")

    logger.info("follow_up agent for %s: %s", public_id, decision.action)
    return decision


if __name__ == "__main__":
    from crm.models import Deal
    from linkedin.browser.registry import cli_parser, cli_session
    from linkedin.db.summaries import materialize_profile_summary_if_missing
    from linkedin.models import Task

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
        from linkedin.models import Campaign
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
        print(f"No Deal found for {public_id}")
        raise SystemExit(1)

    print(f"Running follow-up agent as {session} for {public_id}")
    print(f"Campaign: {session.campaign}")
    print()

    materialize_profile_summary_if_missing(deal, session)
    decision = run_follow_up_agent(session, deal)

    print(f"Action: {decision.action}")
    if decision.message:
        print(f"Message: {decision.message}")
    if decision.reason:
        print(f"Reason: {decision.reason}")
    print(f"Follow-up in: {decision.follow_up_hours}h")
