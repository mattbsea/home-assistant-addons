## 0.1.1 - 2026-09-30

### Fixed

- supergateway crashed the whole add-on ("No connection established for request ID: 0") when a client dropped its request mid-`initialize`, which claude.ai's connector does while connecting. A preloaded guard now logs the error instead of exiting.
- Added a Supervisor watchdog on `/health` so a hung or dead gateway is restarted.

## 0.1.0 - 2026-09-30

### Added

- Initial release wrapping `@playwright/mcp` 0.0.83
- HTTP bridge via supergateway (stateful, one browser per MCP session, 30 min idle timeout)
- 128-bit secret-path URL, generated on first start and persisted (or set via `secret_path`)
- Chromium, Firefox and WebKit preinstalled; selectable with `browser`
- amd64 and aarch64 support
