## 0.1.1 - 2026-03-13

### Fixed

- Point portainer-mcp tools.yaml to `/data/tools.yaml` so it can write to a writable location instead of crashing trying to write to `/usr/local/bin/`

## 0.1.0 - 2026-03-13

### Added

- Initial release wrapping portainer-mcp v0.7.0
- HTTP/SSE bridge via supergateway
- 128-bit secret-path URL generation, persisted across restarts
- amd64 and aarch64 support
