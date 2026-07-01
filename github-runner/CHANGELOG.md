## 0.2.1 - 2026-07-01

### Fixed

- `docker_api: true` was silently doing nothing — no error, `/run/docker.sock` just never got
  mounted into the container, so every job failed with "Cannot reach the Docker socket." Root
  cause: Home Assistant's per-install **Protection Mode** (Settings → Add-ons → shield icon)
  overrides dangerous `config.yaml` grants until explicitly disabled, resetting to on after every
  uninstall/reinstall. This was very likely also the real reason 0.1.x's Docker-in-Docker attempts
  never worked (`full_access: true` never actually behaved like real `--privileged` either).
  Documented as a required one-time step in DOCS.md. **Verified end-to-end** with Protection Mode
  off: real runner registration against a disposable repo, a real `docker build`, and a real
  `docker run` all succeeded.
- Added `hassio_api: true` alongside `docker_api: true`, matching the one other working
  `docker_api` example available in this environment (Portainer's add-on).

## 0.2.0 - 2026-07-01

### Changed

- **Architecture change:** dropped nested Docker-in-Docker entirely — it could not create any
  container on this Supervisor after exhausting every configuration in 0.1.1-0.1.7 (storage
  driver, cgroup driver, runc/Docker version, seccomp/AppArmor, capability combinations, the
  cgroup v2 "no internal process" constraint). Builds now run against the Supervisor's own Docker
  socket via `docker_api: true`. `privileged`/`full_access`/`apparmor` all removed — no longer
  needed since there's no local daemon to run. `run.sh` no longer starts or supervises `dockerd`;
  it just verifies the Docker socket is reachable at startup.
- **Accepted tradeoff:** any workflow this runner executes now has control over every other
  container on the host, not just its own sandbox — `docker_api: true` is documented by Home
  Assistant as dangerous for exactly this reason.

## 0.1.7 - 2026-07-01

### Fixed

- Nested container creation still failed identically (`can't get final child's PID from pipe:
  EOF`) even with cgroupfs pinned and both seccomp and AppArmor confinement explicitly disabled
  per-container — ruling those out. Root cause: Debian bookworm's `docker.io` package ships Docker
  20.10.24 / runc 1.1.5 (2023), which is incompatible with this HAOS host's much newer
  `6.12.20-haos` kernel for nested container creation. Switched to Docker's official upstream apt
  repository (`docker-ce`, `docker-ce-cli`, `containerd.io`) for a current, maintained
  containerd/runc stack. Found by exec'ing `docker run` directly inside the add-on container to
  iterate faster than round-tripping through a full CI job each time.

## 0.1.6 - 2026-07-01

### Fixed

- After the cgroup remount fix, nested container creation still failed:
  `unable to start container process: can't get final child's PID from pipe: EOF`. This base
  image has no functioning systemd/dbus, but dockerd's cgroup-driver auto-detection tried
  `systemd` anyway and hung talking to a dbus that isn't there. Pinned
  `--exec-opt native.cgroupdriver=cgroupfs` explicitly.

## 0.1.5 - 2026-07-01

### Fixed

- Docker builds still failed with `mkdir /sys/fs/cgroup/docker: read-only file system` even with
  `privileged: [SYS_ADMIN, NET_ADMIN]` + `full_access: true` combined — the Supervisor mounts
  `/sys/fs/cgroup` read-only into every add-on container and there's no `config.yaml` key that
  changes that. `run.sh` now remounts it read-write in its own mount namespace at startup
  (`mount --make-rprivate` + `mount -o remount,rw`), which `CAP_SYS_ADMIN` permits even though the
  underlying mount is read-only — this doesn't touch the host's view, only the container's own.
  Found by running the same real docker-build job three times across three different privilege
  configurations before isolating the actual constraint.

## 0.1.4 - 2026-07-01

### Fixed

- `full_access: true` alone does NOT grant Linux capabilities on this Supervisor — inspecting the
  running container showed `Privileged: false` and no added capabilities, just unconfined
  seccomp/apparmor. Replacing `privileged: [SYS_ADMIN, NET_ADMIN]` with `full_access: true` (0.1.2)
  silently dropped both capabilities, so dockerd couldn't even set up its iptables NAT chain
  (`Permission denied (you must be root)`) despite running as uid 0. Now sets **both**
  `privileged: [SYS_ADMIN, NET_ADMIN]` and `full_access: true` together — capabilities from the
  list, unconfined seccomp/apparmor from full_access. Found by inspecting the container's actual
  HostConfig via the Docker API after 0.1.3 also failed to start dockerd.

## 0.1.3 - 2026-07-01

### Fixed

- After switching to `full_access`, the nested `dockerd` failed to start entirely: `failed to
  mount overlay: operation not permitted` / `driver not supported`. The add-on's own container
  root is itself overlayfs on HAOS, and nested overlay-on-overlay isn't supported here. Pinned
  `dockerd --storage-driver=vfs` — no nesting requirement, more disk per layer, which is what the
  USB-backed `data_path` is for. Found by watching the add-on crash-loop on a real restart.

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
