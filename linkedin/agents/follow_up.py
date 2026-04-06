# linkedin/agents/follow_up.py
"""Follow-up agent: reads conversation, returns a structured decision.

Single LLM call with structured output — no tool-calling loop.
The handler in tasks/follow_up.py executes the decision.
"""
from __future__ import annotations

import logging
from typing import Literal

import jinja2
from pydantic import BaseModel, Field, model_validator

from linkedin.conf import PROMPTS_DIR

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


def _format_conversation(messages: list[dict]) -> str:
    """Format synced conversation messages for the prompt."""
    if not messages:
        return "No conversation yet."
    lines = []
    for msg in messages:
        direction = "→" if msg["is_outgoing"] else "←"
        lines.append(f"[{msg['timestamp']}] {direction} {msg['sender']}: {msg['text']}")
    return "\n".join(lines)


def _render_system_prompt(session, profile: dict, conversation_text: str) -> str:
    """Render the agent system prompt from the Jinja2 template."""
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(PROMPTS_DIR)))
    template = env.get_template("follow_up_agent.j2")

    campaign = session.campaign
    self_prof = session.self_profile
    self_name = f"{self_prof.get('first_name', '')} {self_prof.get('last_name', '')}".strip() or session.django_user.username

    return template.render(
        self_name=self_name,
        product_docs=campaign.product_docs or "",
        campaign_objective=campaign.campaign_objective or "",
        booking_link=campaign.booking_link or "",
        full_name=profile.get("full_name", ""),
        headline=profile.get("headline", profile.get("title", "")),
        current_company=profile.get("current_company", ""),
        location=profile.get("location", ""),
        supported_locales=profile.get("supported_locales", []),
        conversation=conversation_text,
    )


def run_follow_up_agent(
    session,
    public_id: str,
    profile: dict,
) -> FollowUpDecision:
    """Read conversation and return a structured follow-up decision.

    Single LLM call — conversation is injected into the prompt,
    and the model returns a FollowUpDecision via structured output.
    """
    from linkedin.db.chat import sync_conversation

    messages = sync_conversation(session, public_id)
    conversation_text = _format_conversation(messages)
    system_prompt = _render_system_prompt(session, profile, conversation_text)

    from linkedin.conf import get_llm
    llm = get_llm(temperature=0.7, timeout=60)
    structured_llm = llm.with_structured_output(FollowUpDecision)
    decision = structured_llm.invoke(system_prompt)
    if decision is None:
        raise RuntimeError(f"LLM returned unparseable response for follow-up of {public_id}")

    logger.info("follow_up agent for %s: %s", public_id, decision.action)
    return decision


if __name__ == "__main__":
    from linkedin.browser.registry import cli_parser, cli_session
    from linkedin.db.deals import get_profile_dict_for_public_id
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
        campaign_id = session.campaign.pk

    profile_dict = get_profile_dict_for_public_id(session, public_id)
    if not profile_dict:
        print(f"No Deal found for {public_id}")
        raise SystemExit(1)

    profile = profile_dict.get("profile") or profile_dict
    profile.setdefault("public_identifier", public_id)

    print(f"Running follow-up agent as {session} for {public_id}")
    print(f"Campaign: {session.campaign}")
    print()

    decision = run_follow_up_agent(session, public_id, profile)

    print(f"Action: {decision.action}")
    if decision.message:
        print(f"Message: {decision.message}")
    if decision.reason:
        print(f"Reason: {decision.reason}")
    print(f"Follow-up in: {decision.follow_up_hours}h")
