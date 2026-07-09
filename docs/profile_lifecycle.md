# Lead & Deal Lifecycle

Every lead flows from discovery on a licensed data source through qualification, a gated paid email lookup, and agentic email follow-up. OpenOutreach is browserless — there is no page navigation, no scraping, and no connect leg.

```
Discover (Lead Finder) → embed → Qualify (LLM) → QUALIFIED ─(GP gate)─▶ READY_TO_FIND_EMAIL
  licensed firmographics                            (Deal)              │ find_email (submit)
                                                                        ▼
                                    free hub hit ─▶ READY_TO_EMAIL   FINDING_EMAIL ─(collect_email poll)─▶ hit: READY_TO_EMAIL
                                                          │           provider job in flight              miss: FAILED (no email)
                                                          ▼
                                              email opener ─▶ EMAILED ⟲ (agentic follow-up) ─▶ COMPLETED / FAILED
```

The authoritative state machine (with every transition and edge case) is in **[`../ARCHITECTURE.md`](../ARCHITECTURE.md) → Deal State Machine**. This page is the narrative summary.

---

## 1. Discovery (licensed, free)

**Where:** `core/pipeline/icp.py` → `core/pipeline/discover.py` → `discovery.py`

An LLM turns the campaign's `product_docs` + `campaign_target` into a Lead Finder **ICP filter** (cached on `Campaign.icp_filters`). `discover()` pages matching firmographic profiles from BetterContact **Lead Finder** — free, no emails — advancing `Campaign.discovery_offset`. Each row is persisted as a `Lead` keyed on `profile_url` (stored, never fetched). Discovery runs when the qualification pool goes dry.

## 2. Embedding (at discovery time)

**Where:** `discovery.py:embed_row` → `core/db/leads.py:create_lead`

The lead's `profile_text` (headline, company description, title, seniority, industry, location) is built from the Lead Finder row and embedded (384-dim `BAAI/bge-small-en-v1.5`) onto `Lead.embedding`. No scrape, no re-fetch.

## 3. Qualification (LLM)

**Where:** `core/pipeline/qualify.py`, `core/ml/qualifier.py`

Embedded leads with no Deal are the pool. The GP selects which candidate to evaluate next — **exploit** (highest predicted probability) when negatives outnumber positives, else **explore** (highest BALD). Every decision is an LLM call over the stored `profile_text`. Cold start (<2 labels of both classes) selects in order.

- **Accepted** → `Lead` promoted to a `Deal` at `QUALIFIED`.
- **Rejected** → `FAILED` Deal with `wrong_fit` outcome (campaign-scoped; not `Lead.disqualified`).

## 4. Rank gate (QUALIFIED → READY_TO_FIND_EMAIL)

**Where:** `core/pipeline/ready_pool.py:promote_to_ready`

A GP confidence gate promotes `QUALIFIED → READY_TO_FIND_EMAIL` when `P(f>0.5) > min_gp_confidence` (0.9). This **rations the paid lookup** — only leads the model is confident about ever cost a credit.

## 5. Find email — two-leg async (READY_TO_FIND_EMAIL → READY_TO_EMAIL / FAILED)

**Where:** `emails/tasks/find_email.py` (submit) + `emails/tasks/collect_email.py` (poll)

`find_email` tries the free cross-operator hub cache first (`contacts.resolve`) — a hit routes straight to `READY_TO_EMAIL`. Otherwise it fires a paid BetterContact job and parks the deal at `FINDING_EMAIL`; `collect_email` polls it (self-chaining backoff):

- **hit** → `READY_TO_EMAIL` (address given back to the hub)
- **miss** (job done, no address) → `FAILED`, `reason="no email"`, **blank outcome** (ML-skipped — an unfindable address is not a fit signal)
- **still running / couldn't-run** → chain the next poll, or past the deadline revert to `READY_TO_FIND_EMAIL` (no credit spent)

The submit leg only fires when there's mailbox send-headroom for the result today, so spend never outruns send capacity.

## 6. Opener (READY_TO_EMAIL → EMAILED)

**Where:** `emails/tasks/send.py` → `core/agents/email_opener.py`

An ungated FIFO queue (paced only by the per-box daily cap) picks the oldest `READY_TO_EMAIL` deal, composes a personalized opener, sends it over SMTP (BCC to the operator's own address), records the outgoing `ChatMessage`, and parks the deal at `EMAILED`. `next_follow_up_at` is seeded from the opener agent's own `follow_up_hours`.

## 7. Agentic follow-up (EMAILED ⟲ → COMPLETED / FAILED)

**Where:** `emails/tasks/follow_up.py` → `core/agents/follow_up.py`

**Full documentation:** [`docs/follow_up_agent.md`](follow_up_agent.md)

A self-rescheduling loop: each due invocation reads IMAP replies (`emails/inbox.py`), folds them into the conversation summary, and asks the LLM for a structured `FollowUpDecision`:

| Action | Effect |
|--------|--------|
| `send_message` | Threaded SMTP reply (`In-Reply-To` = latest, `References` = root); re-arm `next_follow_up_at` |
| `wait` | Push `next_follow_up_at` out, no send |
| `mark_completed` | Close the Deal with the agent's `Outcome` |

The LLM owns pacing via its own `follow_up_hours`. Paced by the per-box daily cap.

## 8. Terminal states

- **COMPLETED** — the agent closed the conversation (booked, declined, or went cold), with an `Outcome`.
- **FAILED** — an unfindable email (`reason="no email"`, blank outcome, ML-skipped) or an LLM qualification rejection (`wrong_fit`, campaign-scoped).

`Lead.disqualified=True` is a separate, permanent account-level exclusion (never given a new deal in any campaign).

## Freemium campaigns

Freemium campaigns draw candidates from a kit-ranked pool (`KitQualifier`) instead of the per-campaign GP, mint the Deal on the fly, and run the **email** funnel like any other campaign. A fraction (`action_fraction`) of activity is devoted to the maintainer-configured promotional campaign.
