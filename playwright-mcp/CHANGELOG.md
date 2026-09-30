## 0.1.0 - 2026-09-30

### Added

- Initial release wrapping `@playwright/mcp` 0.0.83
- HTTP bridge via supergateway (stateful, one browser per MCP session, 30 min idle timeout)
- 128-bit secret-path URL, generated on first start and persisted (or set via `secret_path`)
- Chromium, Firefox and WebKit preinstalled; selectable with `browser`
- amd64 and aarch64 support
