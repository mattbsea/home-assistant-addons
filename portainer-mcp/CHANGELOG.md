## 0.1.7 - 2026-08-20

### Fixed

- Fixed `ReferenceError: crypto is not defined` crashing MCP session initialization. Debian bookworm's apt `nodejs` package is Node 18, which only exposes the global Web Crypto `crypto` object behind an experimental flag; supergateway's bundled `@modelcontextprotocol/sdk` references it directly. Install Node.js 22.x from NodeSource instead of the distro package.

## 0.1.6 - 2026-08-20

### Fixed

- Fixed a severe memory leak: supergateway's stateless streamableHttp mode spawned a new `portainer-mcp` child process per MCP request and relied on the HTTP transport closing to reap it, which MCP clients don't reliably trigger. Over time this accumulated 1,500+ orphaned processes and several GB of RAM, contributing to host-wide memory exhaustion. Switched to `--stateful` mode with a 30-minute `--sessionTimeout`, so exactly one child process is spawned per MCP session and idle sessions are automatically cleaned up.

## 0.1.1 - 2026-03-13

### Fixed

- Point portainer-mcp tools.yaml to `/data/tools.yaml` so it can write to a writable location instead of crashing trying to write to `/usr/local/bin/`

## 0.1.0 - 2026-03-13

### Added

- Initial release wrapping portainer-mcp v0.7.0
- HTTP/SSE bridge via supergateway
- 128-bit secret-path URL generation, persisted across restarts
- amd64 and aarch64 support
