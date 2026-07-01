## 0.1.2 - 2026-07-01

### Fixed

- Docker builds inside a job failed with `unable to apply cgroup configuration: mkdir
  /sys/fs/cgroup/docker: read-only file system`. `privileged: [SYS_ADMIN, NET_ADMIN]` was enough
  for the nested `dockerd` to start and pull images, but not enough for `runc` to actually create
  a container's cgroup. Switched to `full_access: true` (real `--privileged`), which real
  Docker-in-Docker needs. Found by running an actual `docker build` job on a real self-hosted
  runner target.

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
