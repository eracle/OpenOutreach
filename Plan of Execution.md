# Plan of Execution.md

> High-level plan for the feed engagement feature.

---

## Goal
Build a LinkedIn feed commenter that scrapes the user's feed, generates 3 comment options per post using an LLM, presents them in an interactive CLI for approval, and posts the selected comment + reaction.

## Architecture

```
User runs: python manage.py engage_feed
                │
                ▼
     linkedin/actions/feed.py
     ──────────────────────
     Navigate to /feed/
     Scroll to load posts
     Extract: author, text, post URL, post URN
     Return list[FeedPost]
                │
                ▼
     For each new post:
         │
         ▼
     linkedin/llm.py + feed_engagement.j2
     ───────────────────────────────────
     Call LLM with post content
     Returns 3 structured comments + reactions
                │
                ▼
     Interactive CLI prompt (in terminal):
         Post #N — Author
         "[post text preview]"
         
         [1] 🔥 Emotional Friction  — "comment..."
         [2] 🧠 Dismantle Myth      — "comment..."
         [3] 🔄 Reframe Perspective — "comment..."
         [4] ❌ Skip
         
         Choose [1-4]:
                │
                ▼
     linkedin/actions/engage.py
     ─────────────────────────
     Navigate to post URL
     Click comment box
     Type selected comment
     Click post
     Apply reaction (Like/Celebrate/etc.)
     Log to ActionLog
```

## Files to Create/Modify

| File | Status | Purpose |
|------|--------|---------|
| `linkedin/actions/feed.py` | ✅ Done | Scrape feed with Playwright |
| `linkedin/templates/prompts/feed_engagement.j2` | ✅ Done | LLM prompt for 3 comment styles |
| `linkedin/actions/engage.py` | 🔜 Todo | Post comments + reactions |
| `linkedin/management/commands/engage_feed.py` | 🔜 Todo | Interactive CLI command |
| `linkedin/models.py` | 🛠️ Modified | Added ENGAGE action type + rate limit |
| `Decisions.md` | ✅ Done | Architectural decisions log |
| `Progress.md` | ✅ Done | Build progress tracker |
| `Plan of Execution.md` | ✅ Done | This document |
| `Tasks.md` | ✅ Done | Task breakdown |

## Future Considerations
- Could add `--auto` flag for non-interactive mode
- Could add `--dry-run` to preview without posting
- Could add seen-post tracking to avoid duplicates across runs
