# Follow-Up Agent

The follow-up agent manages LinkedIn DM conversations with connected leads. It
runs as a self-rescheduling loop: every decision that isn't `mark_completed`
creates a new Task, so the daemon keeps checking in on each conversation until
the deal is closed.

## Pipeline Overview

```
CONNECTED lead
    │
    ▼
enqueue_follow_up()          ← called from handle_connect / handle_check_pending
    │
    ▼
daemon picks up Task
    │
    ▼
handle_follow_up()           ← linkedin/tasks/follow_up.py
    ├─ rate limit check      ← LinkedInProfile.can_execute(FOLLOW_UP)
    ├─ materialize profile summary (lazy, once per lead×campaign)
    ├─ sync conversation     ← Voyager API → ChatMessage upsert → chat_summary update
    └─ run_follow_up_agent() ← linkedin/agents/follow_up.py
         │
         ▼
    FollowUpDecision
    ┌────────────────────────────────────────────────────┐
    │ send_message   → send DM, record action, re-enqueue│
    │ wait           → re-enqueue (no message sent)       │
    │ mark_completed → close Deal (COMPLETED/FAILED)      │
    └────────────────────────────────────────────────────┘
```

## FollowUpDecision

Structured LLM output defined in `linkedin/agents/follow_up.py`:

| Field | Type | Required When |
|-------|------|---------------|
| `action` | `"send_message"` / `"mark_completed"` / `"wait"` | always |
| `message` | `str` | `send_message` |
| `reason` | `str` | `mark_completed` |
| `follow_up_hours` | `float` | `send_message`, `wait` (defaults to 72) |

Validated by a Pydantic `model_validator` — the LLM call fails if required
fields are missing for the chosen action.

## Agent Context

The agent sees a rich prompt rendered from `follow_up_agent.j2` with:

| Section | Source | Built When |
|---------|--------|------------|
| Seller identity (`self_name`) | `session.self_profile` | every call |
| Product docs, campaign objective, booking link | `Campaign` model | every call |
| Profile facts | `Deal.profile_summary` (JSON fact list) | lazy, once per lead×campaign |
| Chat facts | `Deal.chat_summary` (JSON fact list) | incremental, on each sync |
| Recent messages (verbatim, with age) | last 6 `ChatMessage` rows | every call |
| `days_since_last_outgoing` | computed from messages | every call |
| `unanswered_outgoing` count | trailing run of outgoing messages | every call |

The split between **summary facts** (durable, LLM-extracted) and **verbatim
messages** (recent window) lets the agent reason about the full conversation
history without overflowing the context with old messages.

## Summaries Pipeline

Both summaries live on the `Deal` model as JSON fact lists (`{"facts": [...]}`).
All LLM calls go through `linkedin/db/summaries.py`.

### Profile Summary

`materialize_profile_summary_if_missing(deal, session)`:

1. No-op if `deal.profile_summary` is already populated
2. Re-scrapes the lead's LinkedIn profile via Voyager API
3. Extracts facts via LLM, conditioned on the campaign objective and product docs
4. Persists on `Deal.profile_summary`

Runs **once** per `(lead, campaign)` lifetime — the first time a follow-up
touches the deal.

### Chat Summary

`update_chat_summary(deal, new_messages)`:

1. Called by `sync_conversation()` after upserting new `ChatMessage` rows
2. Formats new messages as a labeled transcript (`[Me]` / `[Lead]`)
3. Short-circuits if there are no incoming (lead) messages — a burst of outgoing
   messages alone doesn't trigger an LLM call
4. Extracts new facts via LLM (`extract_facts`)
5. Reconciles against existing facts via `reconcile_facts()` — mem0-style
   ADD/UPDATE/DELETE/NONE events, not naive append-and-dedup
6. Persists updated list on `Deal.chat_summary`

The reconciliation step uses mem0's `DEFAULT_UPDATE_MEMORY_PROMPT` (vendored at
`linkedin/vendor/mem0/configs/prompts.py`) to decide whether each new fact
should be added, should update an existing fact, should delete a stale fact, or
is redundant (NONE).

## Conversation Sync

`sync_conversation()` in `linkedin/db/chat.py`:

1. Resolves the conversation URN via `find_conversation_urn()` (API scan) with
   `find_conversation_urn_via_navigation()` fallback
2. Fetches messages via Voyager Messaging GraphQL API
3. Upserts into `ChatMessage` by `linkedin_urn` (dedup key)
4. Folds newly-created rows into `deal.chat_summary` via `update_chat_summary()`

## Message Sending

`send_raw_message()` in `linkedin/actions/message.py` tries three strategies in
order, returning `True` on the first success:

| # | Strategy | Method |
|---|----------|--------|
| 1 | **Popup compose** | Open Message popup on profile page, type, send |
| 2 | **Direct thread** | Navigate to `/messaging/thread/new/?recipient=<urn>`, compose, send |
| 3 | **Voyager API** | REST API call via `api/messaging/send.py` |

Each strategy uses the lead's URN (stored on `Lead.urn`). If all three fail,
`handle_follow_up` reverts the Deal to QUALIFIED for re-connection.

## Scheduling & Deduplication

`enqueue_follow_up(campaign_id, public_id, delay_seconds=10)` in
`linkedin/tasks/connect.py`:

- Creates a PENDING `Task` with `scheduled_at = now + delay_seconds`
- **Dedup**: only one FOLLOW_UP task per `(campaign_id, public_id)` exists at a
  time — if one already exists and is pending, it's left untouched

Called from three places:

| Caller | When |
|--------|------|
| `handle_connect()` | profile already CONNECTED (skip connection step) |
| `handle_check_pending()` | connection just accepted (PENDING → CONNECTED) |
| `handle_follow_up()` | self-rescheduling after `send_message` or `wait` |

## Rate Limiting

- Daily limit: `LinkedInProfile.follow_up_daily_limit` (default 30)
- Tracked via `ActionLog` with `action_type=FOLLOW_UP`
- When exhausted: task re-enqueued with **1-hour delay**
- Resets daily; cached in-memory via `LinkedInProfile._exhausted` dict

## Failure Handling

| Failure | Recovery |
|---------|----------|
| Send failed (all 3 strategies) | Deal reverted to QUALIFIED for re-connection |
| No Deal found for public_id | Task skipped with warning |
| Rate limit exhausted | Task re-enqueued in 1 hour |
| LLM returns unparseable output | `RuntimeError` raised, daemon stops |
| 401 / `AuthenticationError` | Daemon re-authenticates, resets task to pending |

## Prompt Heuristics

The system prompt (`follow_up_agent.j2`) instructs the agent to:

- **Language**: infer from profile facts (name origin, location, languages); default to English
- **Tone**: short, casual, warm — like real LinkedIn DMs (1-3 sentences max)
- **No boilerplate**: no placeholders, no signatures, no corporate speak
- **First message**: introduce yourself and the value prop, grounded in profile facts
- **Replies**: respond contextually to the literal phrasing of the last message
- **Timing heuristic**: < 5 days since last outgoing → `wait`; 5-14 days → low-pressure bump; 3+ unanswered outgoing → `mark_completed` as cold
- **Booking link**: include naturally when suggesting a call, not as a standalone line

## CLI Debugging

The agent can be run standalone for debugging:

```bash
# By profile
.venv/bin/python -m linkedin.agents.follow_up --profile john-doe

# By task ID
.venv/bin/python -m linkedin.agents.follow_up --task-id 42
```

Prints the decision (action, message, reason, follow-up hours) without
executing it.
