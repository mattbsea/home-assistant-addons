## 0.2.0 - 2026-09-30

### Added

- Dedicated options for every variable in the self-hosting guide: `auth_secret`, the GitHub App settings, Slack (socket and webhook) and Discord. Unset options are not exported.

## 0.1.0 - 2026-09-30

### Added

- Initial release wrapping `@getpaseo/hub` 0.10.0
- Embedded PGlite database persisted in `/data`, or PostgreSQL via `database_url`
- Optional unattended first account (`bootstrap_*` options)
- `env_vars` for GitHub, Slack and Discord provider apps
- amd64 and aarch64 support
