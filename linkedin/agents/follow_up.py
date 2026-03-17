# linkedin/agents/follow_up.py
"""ReAct agent for agentic follow-up conversations.

Uses a simple tool-calling loop instead of LangGraph's create_agent to avoid
threading issues with Playwright (greenlet-based, single-thread only).
"""
from __future__ import annotations

import json
import logging
from typing import Any

import jinja2
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI  # OpenAI-compatible client (works with any provider)

from linkedin.conf import AI_MODEL, LLM_API_KEY, LLM_API_BASE, PROMPTS_DIR

logger = logging.getLogger(__name__)


def _build_tools(session, public_id: str, profile: dict, campaign_id: int):
    """Build the tool set for the follow-up agent, closed over session context."""

    @tool
    def read_conversation() -> str:
        """Read the conversation history with this lead. Returns formatted messages or 'No conversation yet.'"""
        from linkedin.actions.conversations import get_conversation

        messages = get_conversation(session, public_id)
        if not messages:
            return "No conversation yet."

        lines = []
        for msg in messages:
            lines.append(f"[{msg['timestamp']}] {msg['sender']}: {msg['text']}")
        return "\n".join(lines)

    @tool
    def send_message(message: str) -> str:
        """Send a short LinkedIn message to the lead. Keep it human — 1-3 sentences max."""
        from linkedin.actions.message import send_raw_message

        if not send_raw_message(session, profile, message):
            return "Failed to send message."

        return "Message sent."

    @tool
    def mark_completed(reason: str) -> str:
        """Mark this conversation as completed. Use when: they booked, declined, or went cold."""
        from linkedin.db.deals import set_profile_state
        from linkedin.enums import ProfileState

        set_profile_state(session, public_id, ProfileState.COMPLETED.value, reason=reason)
        logger.info("Agent marked %s as COMPLETED: %s", public_id, reason)
        return f"Marked as completed: {reason}"

    @tool
    def schedule_follow_up(hours: float) -> str:
        """Schedule the next follow-up in N hours from now. Use after sending a message."""
        from linkedin.tasks.connect import enqueue_follow_up

        delay_seconds = hours * 3600
        enqueue_follow_up(campaign_id, public_id, delay_seconds=delay_seconds)
        logger.info("Agent scheduled follow-up for %s in %.1f hours", public_id, hours)
        return f"Follow-up scheduled in {hours} hours."

    return [read_conversation, send_message, mark_completed, schedule_follow_up]


def _count_past_messages(session, public_id: str) -> int:
    """Count saved outgoing ChatMessages for this lead sent by this account."""
    from chat.models import ChatMessage
    from django.contrib.contenttypes.models import ContentType
    from crm.models import Lead

    from linkedin.db.urls import public_id_to_url

    clean_url = public_id_to_url(public_id)
    lead = Lead.objects.filter(website=clean_url).first()
    if not lead:
        return 0
    ct = ContentType.objects.get_for_model(lead)
    return ChatMessage.objects.filter(
        content_type=ct, object_id=lead.pk, owner=session.django_user,
    ).count()


def _get_self_name(session) -> str:
    """Get the logged-in user's name from the /in/me/ sentinel → real profile Lead."""
    import json as _json
    from crm.models import Lead
    from linkedin.db.urls import public_id_to_url
    from linkedin.setup.self_profile import ME_URL

    sentinel = Lead.objects.filter(website=ME_URL).first()
    if not sentinel or not sentinel.description:
        return session.account_cfg.get("handle", "")
    try:
        data = _json.loads(sentinel.description)
        real_id = data["public_identifier"]
    except (ValueError, KeyError, TypeError):
        return session.account_cfg.get("handle", "")
    real_url = public_id_to_url(real_id)
    lead = Lead.objects.filter(website=real_url).first()
    if not lead:
        return session.account_cfg.get("handle", "")
    full = f"{lead.first_name or ''} {lead.last_name or ''}".strip()
    return full or session.account_cfg.get("handle", "")


def _render_system_prompt(session, profile: dict, past_messages_count: int) -> str:
    """Render the agent system prompt from the Jinja2 template."""
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(PROMPTS_DIR)))
    template = env.get_template("follow_up_agent.j2")

    campaign = session.campaign
    self_name = _get_self_name(session)

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
        past_messages_count=past_messages_count,
    )


def run_follow_up_agent(
    session,
    public_id: str,
    profile: dict,
    campaign_id: int,
    *,
    max_iterations: int = 10,
) -> dict[str, Any]:
    """Run the follow-up agent via a simple tool-calling loop.

    Executes tools sequentially in the main thread to stay compatible
    with Playwright's greenlet-based single-thread model.
    """
    llm = ChatOpenAI(
        model=AI_MODEL,
        temperature=0.7,
        api_key=LLM_API_KEY,
        base_url=LLM_API_BASE,
        timeout=60,
    )

    tools = _build_tools(session, public_id, profile, campaign_id)
    tools_by_name = {t.name: t for t in tools}
    llm_with_tools = llm.bind_tools(tools, parallel_tool_calls=False)

    past_messages_count = _count_past_messages(session, public_id)
    system_prompt = _render_system_prompt(session, profile, past_messages_count)

    messages: list = [SystemMessage(content=system_prompt), HumanMessage(content="Begin.")]
    actions_taken: list[dict] = []

    for _ in range(max_iterations):
        response: AIMessage = llm_with_tools.invoke(messages)
        messages.append(response)

        if not response.tool_calls:
            break

        for tc in response.tool_calls:
            tool_name = tc["name"]
            tool_args = tc["args"]
            actions_taken.append({"tool": tool_name, "args": tool_args})

            fn = tools_by_name.get(tool_name)
            if fn is None:
                result = f"Unknown tool: {tool_name}"
            else:
                logger.debug("Agent calling %s(%s)", tool_name, json.dumps(tool_args))
                result = fn.invoke(tool_args)

            messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

    action_names = [a["tool"] for a in actions_taken]
    logger.info(
        "Agent finished for %s: %d messages, %d actions %s",
        public_id,
        len(messages),
        len(actions_taken),
        action_names,
    )

    return {"messages": messages, "actions": actions_taken}


if __name__ == "__main__":
    import os
    import argparse

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "linkedin.django_settings")

    import django
    django.setup()

    from linkedin.conf import get_first_active_profile_handle
    from linkedin.browser.registry import get_or_create_session
    from linkedin.db.deals import get_profile_dict_for_public_id
    from linkedin.models import Task

    logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Run the follow-up agent for a profile")
    parser.add_argument("--handle", default=None, help="LinkedIn handle (default: first active)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--profile", help="Public identifier of the target profile")
    group.add_argument("--task-id", type=int, help="Task ID to run the agent for")
    args = parser.parse_args()

    handle = args.handle or get_first_active_profile_handle()
    if not handle:
        print("No active LinkedInProfile found.")
        raise SystemExit(1)

    session = get_or_create_session(handle=handle)
    session.campaign = session.campaigns.first()
    session.ensure_browser()

    if args.task_id:
        task = Task.objects.get(pk=args.task_id)
        public_id = task.payload["public_id"]
        campaign_id = task.payload["campaign_id"]
        # Set the correct campaign from the task
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

    print(f"Running follow-up agent as @{handle} for {public_id}")
    print(f"Campaign: {session.campaign}")
    print()

    result = run_follow_up_agent(session, public_id, profile, campaign_id)

    print("\n--- Agent Actions ---")
    for action in result["actions"]:
        print(f"  {action['tool']}({action['args']})")

    if not result["actions"]:
        print("  (no actions taken)")
