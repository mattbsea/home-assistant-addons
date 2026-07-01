## 0.1.0 - 2026-06-30

### Added

- Initial release: self-hosted GitHub Actions runner(s), one per configured repo/org target
- Docker-in-Docker (privileged) so container image build/push workflows work out of the box
- Persistent runners with a shared Docker daemon and build cache on the mounted USB disk (`media:rw`)
- Ingress status page showing each target's online/idle/busy state
