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
