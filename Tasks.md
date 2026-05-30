# Tasks.md

> Granular task breakdown for the feed engagement feature.

---

## 🔜 Phase 2: Comment Posting

### Task 2.1 — Create `linkedin/actions/engage.py`
**Status:** ⏳ Not Started
**Dependencies:** None
**Description:** Playwright action module for posting comments and applying reactions to LinkedIn posts.

**Details:**
- Navigate to post URL
- Locate the comment textbox (multiple selector fallbacks like other action modules)
- Type the comment with human-like delays
- Click the comment post button
- Locate the reaction button on the post
- Apply the selected reaction (Like/Celebrate/Support/Love/Insightful/Funny)
- Handle errors gracefully (dump page HTML on failure, like other actions)
- Log success/failure
- Returns True/False

**Estimated effort:** Medium (1-2 hours)

---

### Task 2.2 — Create `linkedin/management/commands/engage_feed.py`
**Status:** ⏳ Not Started
**Dependencies:** Task 2.1, feed.py, feed_engagement.j2
**Description:** Django management command that implements the interactive CLI workflow.

**Details:**
- Extends `BaseCommand`
- Uses `cli_session()` from browser registry (like other commands)
- Calls `scrape_feed()` to get new posts
- For each post: calls LLM via `run_agent_sync()` + Jinja2 template
- Parses the JSON response into 3 comment options
- Displays in terminal with colored prompts (use `termcolor` like daemon.py)
- Reads user selection
- Calls `engage.post_comment_and_reaction()` if selected
- Rate limits via `LinkedInProfile.can_execute('engage')` and `record_action()`
- Handles `KeyboardInterrupt` gracefully

**Estimated effort:** Medium (2-3 hours)

---

## ❌ Phase 3: Polish & Review

### Task 3.1 — Run end-to-end test
**Status:** ❌ Not Started
**Dependencies:** All of Phase 2
**Description:** Run the command and verify it works.

### Task 3.2 — Update docs
**Status:** ❌ Not Started
**Dependencies:** Phase 2 complete
**Description:** Update CLAUDE.md and ARCHITECTURE.md with new modules.

---

## ✅ Completed Tasks

### Task 0.1 — Create project documents
- [x] Decisions.md
- [x] Progress.md
- [x] Plan of Execution.md
- [x] Tasks.md

### Task 0.2 — Update `linkedin/models.py`
- [x] Added `ENGAGE` to `ActionLog.ActionType`
- [x] Added `engage` to `_RATE_LIMIT_FIELDS`

### Task 1.1 — Create `linkedin/actions/feed.py`
- [x] Feed scraping via Playwright
- [x] `FeedPost` dataclass
- [x] Scroll + extract logic
- [x] Deduplication by text content
- [x] `__main__` for standalone testing

### Task 1.2 — Create `linkedin/templates/prompts/feed_engagement.j2`
- [x] 3 comment styles with detailed instructions
- [x] Indian financial planning context
- [x] Structured JSON output format
- [x] Reaction selection per comment
