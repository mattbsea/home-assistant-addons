## 0.2.2 - 2026-04-12

### Changed

- Switch from SSE to Streamable HTTP transport (required for Claude Desktop)
  - SSE is deprecated in the MCP spec; Claude Desktop remote connectors require Streamable HTTP
  - MCP endpoint changes from `/sse` to `/mcp`
  - Update your Claude Desktop connector URL: `http://<ha-ip>:9565/<secret_path>/mcp`
- Upgrade `mcp` package requirement from `>=1.0.0` to `>=1.27.0`

## 0.2.1 - 2026-03-31

### Fixed

- Remove incorrect `resp.close()` call from 401 retry path. httpx's AsyncClient
  already closes every response automatically (via `aread()`) before returning it
  to application code. Calling the sync `close()` on an async response raises
  `RuntimeError: Attempted to call an sync close on an async stream`, which would
  have broken 401 retry handling if a token ever expired unexpectedly.

## 0.2.0 - 2026-03-31

### Fixed

- Memory leak: replaced supergateway (Node.js) with native Python SSE transport
  - supergateway v3.4.3 accumulated tracking state in Node.js heap for each
    stateless MCP request, growing to 8GB+ after days of multi-session use
  - Python's mcp library (FastMCP + uvicorn) handles HTTP natively with no leaks
- Removed Node.js, npm, and supergateway from the container image (~200MB smaller)

### Changed

- MCP endpoint URL suffix changed from `/mcp` (streamableHttp) to `/sse` (SSE)
  - Update your Claude Code MCP config: `http://<ha-ip>:9565/<secret_path>/sse`

## 0.1.1 - 2026-03-18

### Fixed

- Remove duplicate port option (now only in Network section)
- Auto-generate secret path on first start if not set in options
- Add /data volume for persisting generated secret path

## 0.1.0 - 2026-03-18

### Added

- Initial release with Python MCP server for Nginx Proxy Manager
- Full CRUD for proxy hosts, redirection hosts, streams, dead hosts
- SSL certificate management (list, create, delete, renew)
- Access list management (list, create, update, delete)
- Read-only access to settings, audit log, and host reports
- HTTP bridge via supergateway with streamableHttp transport
- User-configurable secret-path URL and port
- amd64 and aarch64 support
