# Follow-Up Agent

The follow-up agent runs the agentic **email** conversation on a deal that has been emailed. It is a self-rescheduling loop: every decision that isn't `mark_completed` re-arms the deal's clock, so the daemon keeps checking back until the conversation ends.

## Flow

```
EMAILED deal, due by next_follow_up_at
        │
handle_follow_up()            ← emails/tasks/follow_up.py (drains the oldest due EMAILED deal with box headroom)
        │
        ├─ read replies        ← emails/inbox.py:sync_inbox (IMAP: match the thread root in References/In-Reply-To,
        │                         upsert new replies as ChatMessage, fold into chat_summary)
        └─ run_follow_up_agent()  ← core/agents/follow_up.py
```

## Decision

`run_follow_up_agent` builds context (campaign docs + booking link, the lead's `profile_summary`, the `chat_summary`, and a recency window of verbatim messages) and makes **one** structured LLM call returning a `FollowUpDecision`:

| Action | Effect |
|--------|--------|
| `send_message` | Threaded SMTP reply via `emails/sender.py` (`In-Reply-To` = latest message, `References` = thread root); records the outgoing `ChatMessage`; re-arms `next_follow_up_at` from the agent's own `follow_up_hours`. |
| `wait` | Push `next_follow_up_at` out, no send. |
| `mark_completed` | Close the Deal `COMPLETED` with the agent's `Outcome`. |

The LLM owns pacing end-to-end via `follow_up_hours` (there is no hardcoded default). Sends are bounded by the per-mailbox daily cap.

## Summaries

All summary LLM calls go through `core/db/summaries.py` (mem0-style):

- `materialize_profile_summary_if_missing(deal, session)` builds `profile_summary` on first follow-up touch from the lead's **stored** `profile_text` — no re-scrape (there is no profile to fetch).
- `update_chat_summary(deal, new_messages, seller_name=…)` folds newly-read replies into `chat_summary` via `reconcile_facts` (mem0 ADD/UPDATE/DELETE/NONE). The mem0 update prompt is vendored under `core/vendor/mem0/`.

## Prompts

The opener and follow-up agents share a base template; both live in `core/templates/prompts/`: `_outreach_base.j2` (shared), `email_opener.j2` (the one-shot cold opener, `core/agents/email_opener.py`), and `follow_up_agent.j2` (this agent). See [Template Variables](./template-variables.md).
