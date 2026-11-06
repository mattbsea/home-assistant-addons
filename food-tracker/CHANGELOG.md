## 1.0.0 - 2026-11-06

### Added

- Initial release: the nutrition-mcp fork (SQLite, no Supabase) as a single Home Assistant add-on, built from its own repository at the commit pinned in `build.yaml` (`APP_REF`).
- MCP endpoint at `/mcp/<mcp_key>` for Claude custom connectors (secret URL path, no OAuth).
- Apple Health shortcut API at `/api/v1/health-sync/*`, authenticated with its own `shortcut_key`.
- Options `mcp_key`, `shortcut_key`, `dashboard_password`, `usda_api_key` and `timezone`; blank keys are generated and kept in `/data/keys.json`.
- `show-keys` helper to print the stored keys from inside the container.
- Bun 1.4.4 pinned by image digest; amd64 and aarch64 (Bun has no armv7 build).
- `backup: cold`, so the SQLite database is checkpointed and closed before Supervisor copies `/data`.
- Watchdog on the unauthenticated `/health` endpoint.
