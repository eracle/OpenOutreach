# MCP Server (V1)

OpenOutreach now includes a built-in MCP server under `mcp_server/`.

## Run

```bash
python -m mcp_server
```

The server runs over stdio and boots Django with `linkedin.django_settings`.

## Available tools

- `get_pipeline_stats`
- `list_profiles_by_state`
- `get_profile`
- `get_qualification_reason`
- `render_followup_preview`
- `set_profile_state`

## Notes

- `set_profile_state` is guarded with strict allowed transitions:
  - `new -> pending|connected|failed`
  - `pending -> connected|failed`
  - `connected -> completed|failed`
  - `completed` and `failed` are terminal (idempotent self-transition only)
- The current scope is CRM/data orchestration. It does not trigger live Playwright actions.
- `list_profiles_by_state` returns both `returned_count` (after `limit`) and `total_count` (full matching set).
