# openoutreach/core/agents/email_opener.py
"""Email opener agent: composes the single Layer-1 cold email for a deal.

A distinct entrypoint from the follow-up agent. Layer 1 email is one outbound
touch — no thread to read, no send/wait/give-up decision — so the structured
output is just a subject + body. The prompt shares the outreach base (identity,
product docs, lead summary, Mom Test strategy, language/no-placeholder rules) via
``email_opener.j2`` and only adds the cold-email framing + the subject request.
The multi-turn email conversation is the hosted Layer-2 backend's job.
"""
from __future__ import annotations

import logging

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from openoutreach.core.agents.prompt import base_context, render
from openoutreach.core.llm import get_llm_model, run_agent_sync

logger = logging.getLogger(__name__)


class EmailDraft(BaseModel):
    """Structured output from the email opener agent."""

    subject: str = Field(description="The email subject line — short, specific, like a real person wrote it; not salesy.")
    body: str = Field(description="The email body. A few short sentences; no signature, no placeholders.")
    follow_up_hours: float = Field(
        description="Hours to wait before the first follow-up if they don't reply. You decide the pace; 48-72h is a reasonable cold-open default.",
    )


def compose_opener_email(session, deal) -> EmailDraft:
    """Compose the opener subject + body for ``deal`` from its summaries + campaign docs."""
    system_prompt = render("email_opener.j2", **base_context(session, deal))

    agent = Agent(
        get_llm_model(),
        output_type=EmailDraft,
        model_settings={"temperature": 0.7, "timeout": 60},
    )
    draft = run_agent_sync(agent.run(system_prompt)).output
    if draft is None:
        raise ValueError(f"email opener returned no draft for {deal.lead.public_identifier}")
    return draft
