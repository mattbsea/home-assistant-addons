# Changelog

## 1.0.0

### Added
- Initial release: runs the official `rustdesk-server` `hbbs` (ID/rendezvous) and `hbbr` (relay)
  binaries, with automatic restart on crash and a persistent identity keypair under `/data`.
- Ingress status/connection dashboard: live `hbbs`/`hbbr` health, the server's public key,
  ID/relay server addresses, the ports that need forwarding for remote access, and recent logs.
- Configuration options for `relay_host`, `encrypted_only`, and an optional `custom_key`.
