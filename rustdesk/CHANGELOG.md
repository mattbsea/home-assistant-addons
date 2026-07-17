# Changelog

## 1.0.0

### Added
- Initial release: a browser-accessible RustDesk client (`linuxserver/docker-rustdesk`, streamed
  via Selkies) with the official `rustdesk-server` `hbbs`/`hbbr` binaries layered on top, both
  reachable from the Home Assistant sidebar over ingress.
- `hbbs`/`hbbr` supervised and auto-restarted by the image's s6-overlay init.
- Persistent server identity keypair and client settings under `/data`.
- Configuration options for `relay_host`, `encrypted_only`, and an optional `custom_key`.
