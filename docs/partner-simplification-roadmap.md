# Partner Campaign Simplification Roadmap

Tracking remaining items to reduce partner-related cyclomatic complexity.

## Done

- [x] **Split connect task types** — Dedicated `connect_partner` task type with its own handler in
  `tasks/connect_partner.py`. Removed probabilistic gating; `action_fraction` now controls reschedule delay (
  `base_delay / fraction`), giving deterministic 1:N ratio between partner and regular connects.

- [x] **~~Clean up partner logging boilerplate~~** — All task handlers now use `[{campaign_name}]` prefix with
  `logging.INFO`. No more `PARTNER_LOG_LEVEL` / `is_partner` checks in task handlers.

- [x] **Eliminate `seed_partner_deals` + remove `is_partner` from CRM queries** (items 2 + 4) — Partner candidates are
  now selected directly from `ProfileEmbedding` via `get_partner_candidate()` in `pipeline/partner_pool.py`. A Deal is
  created just-in-time via `create_partner_deal()` only for the selected candidate, not bulk-created upfront. This
  eliminated the O(n) scan per iteration, removed all `is_partner` branches from `get_qualified_profiles`,
  `count_qualified_profiles`, and `get_ready_to_connect_profiles`, and removed the `threshold <= 0` shortcut from
  `promote_to_ready`. Partner campaigns no longer flow through the Deal-based pool system (`pools.py` / `ready_pool.py`)
  at all.

- [x] **Merge connect handlers + remove `connect_partner` task type** (items 1 + 2 combined) — Unified
  `handle_connect` and `handle_connect_partner` into a single handler using a `ConnectStrategy` dataclass. The strategy
  factory (`strategy_for()`) checks `campaign.is_partner` and returns the right candidate source, pre-connect hook,
  delay, and qualifier. The handler itself has zero branches — all partner knowledge is in the factory. Removed
  `CONNECT_PARTNER` from `Task.TaskType` (merged into migration `0010_task.py`). Deleted `tasks/connect_partner.py`.

## Next Steps

### 1. Unify the qualifier/pipeline interface

The `partner_qualifier` + `kit_model` pair is threaded through the daemon and handler signatures:
`run_daemon → handler → strategy_for → get_partner_candidate → rank_profiles`. Instead, each campaign could carry its
own qualifier object (partner campaigns wrap `kit_model` in a qualifier-compatible adapter). This eliminates the separate
`partner_qualifier` arg from all handler signatures and simplifies `strategy_for()`.
