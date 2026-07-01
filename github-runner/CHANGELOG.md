## 0.1.1 - 2026-06-30

### Fixed

- Status page crashed on startup: `bashio::config 'targets'` emits bare newline-separated JSON
  objects (or nothing for an empty list), not a JSON array — `GH_STATUS_TARGETS` now goes through
  `jq -s -c` to produce the array `server.py` actually expects, including a correct `[]` for zero
  targets. Found by installing and starting the add-on on a real Home Assistant instance.

## 0.1.0 - 2026-06-30

### Added

- Initial release: self-hosted GitHub Actions runner(s), one per configured repo/org target
- Docker-in-Docker (privileged) so container image build/push workflows work out of the box
- Persistent runners with a shared Docker daemon and build cache on the mounted USB disk (`media:rw`)
- Ingress status page showing each target's online/idle/busy state
