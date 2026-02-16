# 00 — Template Refactoring

## Decisions Made

- **Deprecate Jinja-only mode**: the `jinja` template type (raw Jinja2 rendering without LLM) is removed. All message rendering goes through `ai_prompt` mode (template rendered with context, then sent to LLM).
- **Use `followup2.j2` as the default prompt**: the existing `assets/templates/prompts/followup2.j2` (2-4 sentences, 400 chars max) becomes the single default follow-up prompt. Add `product_description` as a new variable.
- **Deprecate connection note**: connection requests will no longer include a custom note (feature being removed). This affects the connect lane (`lanes/connect.py`) and any connection action code (`actions/connect.py`) — remove the note parameter and any related template rendering.
- **One default prompt template**: all campaigns share the same default prompt. Per-campaign override is possible but not required for initial implementation.
- **New template variable**: `product_description` is injected into the follow-up prompt context alongside the existing profile fields (`first_name`, `last_name`, `headline`, `positions`, etc.).

## Current State

- `templates/renderer.py`: `render_template()` supports two modes (`jinja`, `ai_prompt`). Template type is per-account in `accounts.secrets.yaml`.
- Per-account config: `followup_template` (path), `followup_template_type` (`jinja` or `ai_prompt`), `booking_link`.
- Templates do NOT currently receive `product_docs` or `campaign_objective` — only profile fields.
- Several hardcoded OpenOutreach-branded templates exist (`followup.j2`, `followup_brand.j2`, `_brand_description.j2`).

## What To Do

1. Remove `jinja` mode from `render_template()` — always run through LLM.
2. Remove `followup_template_type` from account config (no longer needed).
3. Add `product_description` to the context passed to `render_template()`. Source: currently `PRODUCT_DOCS_FILE`, later from Campaign model.
4. Update `followup2.j2` to use `{{ product_description }}` in the prompt.
5. Remove or archive unused templates (`followup.j2` jinja-only, `_brand_description.j2`, `followup_brand.j2`).
6. `booking_link` remains as-is (appended post-render).

## Open Questions

- None. This is self-contained and can be implemented independently.
