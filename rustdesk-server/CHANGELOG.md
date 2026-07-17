# Changelog

## 1.0.0

### Added
- Initial release: the official `rustdesk-server` `hbbs`/`hbbr` binaries, headless, on a plain
  Debian base — no GUI, no privileged access, no ingress. Replaces the old single `rustdesk`
  add-on, which incorrectly built these binaries on top of `linuxserver/docker-rustdesk` (a
  privileged GUI desktop client image, not a server — see this add-on's DOCS.md for why).
- `hbbs`/`hbbr` supervised and independently restarted on crash by `run.sh`.
- Persistent server identity keypair under `/data`.
- Configuration options for `relay_host`, `encrypted_only`, and an optional `custom_key`.
