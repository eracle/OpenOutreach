# Decisions.md

> A running log of architectural and design decisions for the feed engagement feature.

---

## D-001: Interactive CLI Workflow
**Date:** 2026-05-30
**Status:** Active

**Decision:** The feed engagement feature will be an interactive CLI command (`python manage.py engage_feed`), not a background daemon task.

**Rationale:** The user wants to review and approve each comment before it gets posted. A background daemon would push comments without oversight. An interactive CLI lets the user see each post and its 3 generated options, then pick which one (if any) to post — same review-approve loop the user wants.

---

## D-002: 3 Comment Styles Per Post
**Date:** 2026-05-30
**Status:** Active

**Decision:** The LLM will generate 3 comments per post, one for each style:
1. Emotional Friction — challenges assumptions productively
2. Dismantle Myth — names and refutes the implicit narrative
3. Reframe Perspective — offers a genuine shift in viewpoint

Each comment gets a recommended reaction (Like/Celebrate/Support/Love/Insightful/Funny).

**Source:** User-provided prompt (pasted text).

---

## D-003: LLM Decides Reaction Per Comment
**Date:** 2026-05-30
**Status:** Active

**Decision:** The LLM selects the reaction for each comment based on the post content and comment angle. No hardcoded rules.

**Rationale:** User said "I will let you decide how to react."

---

## D-004: No New Django Models
**Date:** 2026-05-30
**Status:** Active

**Decision:** Feed posts and suggestions live in-memory for the duration of the interactive session. No DB persistence.

**Rationale:** The user reviews and approves immediately — there's no need to store pending suggestions. Simplifies the architecture and avoids model migrations.

---

## D-005: One ActionLog Entry Per Posted Comment
**Date:** 2026-05-30
**Status:** Active

**Decision:** Each successfully posted comment is recorded as an `ActionLog` entry with `action_type='engage'` for rate-limiting purposes.

**Implementation:** Added `ENGAGE` to `ActionLog.ActionType` and `"engage"` to `_RATE_LIMIT_FIELDS` in `linkedin/models.py`.

---

## D-006: Feed Scraping via Playwright (UI), Comments via Playwright (UI)
**Date:** 2026-05-30
**Status:** Active

**Decision:** Both feed scraping and comment posting use Playwright UI automation, not the Voyager API. This mirrors how the rest of OpenOutreach works (connect, message) and keeps the behavior within LinkedIn norms.

**Architecture:** `linkedin/actions/feed.py` for scraping, `linkedin/actions/engage.py` for posting.
