# Remove profile_dict pattern

## Problem

`to_profile_dict()` builds a `{"lead_id", "public_identifier", "url", "profile", "meta"}` dict that gets threaded through qualifiers, pools, and task handlers. Callers then unpack it with `p.get("lead_id")`, `p.get("public_identifier")`, etc. — all fields that already exist on Lead/Deal.

Now that lazy accessors live on Lead (`get_profile`, `get_embedding`), the intermediate dict is unnecessary.

## Scope

- Remove `Lead.to_profile_dict()`, `_deal_to_profile_dict()`, `get_profile_dict_for_public_id()`
- Update qualifier interfaces (`rank_profiles`, `explain`, `_load_profile_embeddings`) to take Lead objects
- Update pool functions (`promote_to_ready`, `find_ready_candidate`, `find_freemium_candidate`) to pass Lead/Deal directly
- Update task handlers (`handle_check_pending`, `handle_follow_up`) to work with Deal objects
- Update `get_leads_for_qualification()` and `get_qualified_profiles()` to return querysets or model lists
- Remove `meta` dict packing (connect_attempts, backoff_hours live on Deal already)

## Acceptance Criteria

- No `profile_dict` pattern anywhere in the codebase
- All qualifier/pool/task functions take model instances
- All tests pass
