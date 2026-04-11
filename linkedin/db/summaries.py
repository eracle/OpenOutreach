"""mem0-style fact-list summaries for Deal profile and chat history.

Single LLM boundary for the lazy summary pipeline. Summaries are stored as
JSON fact lists on `Deal.profile_summary` and `Deal.chat_summary`. Both are
campaign-scoped derived caches: deleting them and re-running the lazy path
rebuilds them from source (a Voyager re-scrape for `profile_summary`,
`ChatMessage` rows for `chat_summary`).
"""
from __future__ import annotations

import logging
from typing import Iterable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# Vendored fact-extraction prompt — modeled on mem0's FACT_RETRIEVAL_PROMPT.
# Kept inline so we don't pull mem0ai's transitive deps (qdrant, grpcio,
# sqlalchemy, posthog, ~12 MB) just for one constant string.
_FACT_EXTRACTION_PROMPT = """\
You are an information-extraction assistant. Your job is to read the input
text and produce a flat list of atomic, self-contained factual statements
about the subject.

Rules:
- Each fact must be a complete sentence that stands on its own.
- Prefer concrete, durable facts (identity, role, employer, location, career
  arc, stated goals, expressed concerns) over fleeting commentary.
- Do not invent facts. If the text does not assert it, do not include it.
- Do not duplicate facts. Merge near-duplicates.
- Keep each fact short (under ~25 words).
- Return between 0 and 30 facts. Empty list is acceptable when there is
  nothing useful to extract.

Output a JSON object matching the schema you have been given.
"""


class FactList(BaseModel):
    """Structured LLM output for fact extraction."""

    facts: list[str] = Field(
        default_factory=list,
        description="Atomic, self-contained factual statements extracted from the input text.",
    )


# ── LLM boundary ──

def extract_facts(text: str, *, context: str = "") -> list[str]:
    """Extract a flat list of atomic facts from `text`.

    `context` is an optional preamble (campaign objective, product docs) that
    biases what counts as a relevant fact. Returns `[]` for empty inputs.
    """
    if not text or not text.strip():
        return []

    from langchain_openai import ChatOpenAI

    from linkedin.conf import get_llm_config

    llm_api_key, ai_model, llm_api_base = get_llm_config()
    if not llm_api_key:
        raise ValueError("LLM_API_KEY is not set in Site Configuration.")

    system = _FACT_EXTRACTION_PROMPT
    if context:
        system = f"{system}\n\nContext for relevance:\n{context}"

    llm = ChatOpenAI(
        model=ai_model,
        temperature=0.0,
        api_key=llm_api_key,
        base_url=llm_api_base,
        timeout=60,
    )
    structured = llm.with_structured_output(FactList)
    result: FactList = structured.invoke([
        {"role": "system", "content": system},
        {"role": "user", "content": text},
    ])
    return list(result.facts)


# ── Profile summary ──

def materialize_profile_summary_if_missing(deal, session) -> None:
    """Build `deal.profile_summary` lazily on first follow-up touch.

    Re-scrapes the lead via Voyager once per `(lead, campaign)` lifetime,
    extracts facts conditioned on the campaign objective + product docs,
    persists them on the Deal. No-op if already built.
    """
    if deal.profile_summary:
        return

    lead = deal.lead
    profile = lead.get_profile(session)
    if not profile:
        logger.warning(
            "materialize_profile_summary: empty profile for deal=%s lead=%s",
            deal.pk, lead.public_identifier,
        )
        return

    from linkedin.ml.profile_text import build_profile_text

    profile_text = build_profile_text({"profile": profile})
    context_parts = []
    campaign = deal.campaign
    if getattr(campaign, "campaign_objective", None):
        context_parts.append(f"Campaign objective: {campaign.campaign_objective}")
    if getattr(campaign, "product_docs", None):
        context_parts.append(f"Product context: {campaign.product_docs}")
    context = "\n\n".join(context_parts)

    facts = extract_facts(profile_text, context=context)
    deal.profile_summary = {"facts": facts}
    deal.save(update_fields=["profile_summary"])
    logger.info(
        "profile_summary built for deal=%s lead=%s (%d facts)",
        deal.pk, lead.public_identifier, len(facts),
    )


# ── Chat summary ──

def _format_messages_for_extraction(messages: Iterable) -> str:
    """Render new ChatMessages as a `Me:`/`Lead:` transcript for fact extraction."""
    lines = []
    for m in messages:
        speaker = "Me" if m.is_outgoing else "Lead"
        content = (m.content or "").strip()
        if content:
            lines.append(f"{speaker}: {content}")
    return "\n".join(lines)


def update_chat_summary(deal, new_messages) -> None:
    """Fold newly-synced ChatMessages into `deal.chat_summary` incrementally.

    Existing facts are preserved; only new messages are sent to the LLM.
    Empty input is a no-op (e.g., a sync that found no new messages).
    """
    new_messages = list(new_messages)
    if not new_messages:
        return

    formatted = _format_messages_for_extraction(new_messages)
    if not formatted:
        return

    new_facts = extract_facts(formatted)
    if not new_facts:
        return

    existing = (deal.chat_summary or {}).get("facts", [])
    merged = _merge_facts(existing, new_facts)
    deal.chat_summary = {"facts": merged}
    deal.save(update_fields=["chat_summary"])
    logger.info(
        "chat_summary updated for deal=%s (+%d facts → %d total)",
        deal.pk, len(new_facts), len(merged),
    )


def _merge_facts(existing: list[str], new: list[str]) -> list[str]:
    """Append new facts that aren't already present (case-insensitive match)."""
    seen = {f.strip().lower() for f in existing}
    merged = list(existing)
    for fact in new:
        key = fact.strip().lower()
        if key and key not in seen:
            merged.append(fact)
            seen.add(key)
    return merged
