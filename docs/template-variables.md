# Prompt Context Reference

This describes what the outreach agents' prompts receive. The prompts live in `core/templates/prompts/` (`_outreach_base.j2` shared, plus `email_opener.j2` and `follow_up_agent.j2`); the context is assembled in **`core/agents/prompt.py`** (`base_context`, `_format_facts`).

There is **no Voyager profile dict** — the browser/scraping channel was removed. Lead context comes from the licensed Lead Finder payload that was stored at discovery, not from a live fetch.

## What the prompts receive

- **Campaign context** — `product_docs`, `campaign_target`, and `booking_link` from the `Campaign`.
- **Seller identity** — the operator's name (`seller_name_from(session)`, synthesized from the Django `User` / `SiteConfig`, not scraped), used to keep the LLM from misattributing greetings in a reply.
- **Lead facts** — the deal's `profile_summary`: a mem0-style JSON fact list materialized once from the lead's **stored `profile_text`** (headline, company description, title, seniority, industry, location). No positions/education/URNs — those came from the retired scrape and no longer exist.
- **Conversation facts** (follow-up only) — the deal's `chat_summary` (running fact list folded from IMAP-read replies) plus a recency window of verbatim `ChatMessage`s.

## Outputs

- Opener → `EmailDraft{subject, body, follow_up_hours}`.
- Follow-up → `FollowUpDecision{action, message?, outcome?, follow_up_hours}`.

To see the exact fields passed to a template, read `core/agents/prompt.py:base_context` and the agent that renders it (`core/agents/email_opener.py` / `core/agents/follow_up.py`). The fact-list shapes are produced by `core/db/summaries.py`.
