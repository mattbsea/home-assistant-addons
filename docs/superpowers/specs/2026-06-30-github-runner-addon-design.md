# GitHub Actions Self-Hosted Runner Add-on — Design

## Purpose

Provide a Home Assistant add-on (`github_runner`) that runs one or more
self-hosted GitHub Actions runners, so multi-arch container image builds
that run longer than GitHub-hosted runners comfortably support can happen
on local hardware instead.

No existing Home Assistant community add-on provides this (checked
`hassio-addons/repository` and web search — nothing found).

## Scope

- amd64 only (matches the target host; avoids nested-Docker-in-Docker
  quirks on ARM kernels).
- Supports an arbitrary, user-configured list of targets from day one —
  each target is either a single repo or a single GitHub org. Repos may
  be personal-account repos or org repos; a mix is expected.
- Runners are **persistent** (not ephemeral): they stay registered and
  reuse their container/build cache across many jobs. Acceptable because
  every target is a repo the user personally controls — the security
  benefit of ephemeral (fresh state per job) isn't needed here, and
  persistent mode keeps Docker layer caches warm across builds.
- Primary workload is building and pushing container images
  (`docker build` / multi-arch buildx), so the add-on needs real
  Docker-engine access, not just a bare compile toolchain.

## Architecture

```
┌───────────────────────────────────────────────────────────┐
│ github_runner add-on container (privileged)                 │
│                                                                │
│  dockerd  --data-root <data_path>/docker                      │
│           (shared by all runner processes; layer cache lives  │
│            on the USB disk, not the HAOS system partition)    │
│                                                                │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐              │
│  │ runner proc │  │ runner proc │  │ runner proc │  ... one per   │
│  │ target A    │  │ target B    │  │ target C    │      configured │
│  │ _work → <data_path>/runners/<name>/_work       │      target    │
│  └────────────┘  └────────────┘  └────────────┘              │
│                                                                │
│  status webserver (ingress) — polls GitHub API for each        │
│  target's runner online/idle/busy state                       │
└───────────────────────────────────────────────────────────┘
```

- **Docker access**: Docker-in-Docker, privileged (`privileged: [SYS_ADMIN]`
  in `config.yaml`). Chosen over mounting the host's Docker socket
  (`docker_api: true`) because DinD keeps this add-on's Docker daemon
  fully isolated from HAOS's own Supervisor-managed containers — a
  compromised or malicious CI job can only affect its own throwaway
  daemon, not the host. Chosen over rootless Podman because rootless
  mode is slower and more likely to hit friction on HAOS's kernel/storage
  support; the privileged-DinD tradeoff (heavier, more disk) is
  acceptable since disk now lives on the USB mount.
- **Docker daemon sharing**: one shared `dockerd` for all runner
  processes/targets, not one daemon per target. All targets are the
  user's own trusted repos, so there's no isolation benefit to
  per-target daemons — only wasted disk/memory/startup time.
- **Storage**: the USB disk is already configured as a Home Assistant
  Supervisor Mount with usage `media`, visible on the host at
  `/mnt/data/supervisor/media/usbdisk`. The add-on requests
  `map: [media:rw]`, which surfaces it inside the container at
  `/media/usbdisk`. Both Docker's `data-root` and each runner's `_work`
  (job checkout) directory live under a configurable `data_path`
  (default `/media/usbdisk/github-runner`) on that disk, so job checkouts
  and image layer caches don't compete with the HAOS system partition
  for space. This only works because the mount is a local block device
  formatted with a real filesystem (ext4/xfs) — Docker's overlay2 storage
  driver cannot run on a network filesystem (NFS/SMB).

## Configuration

`config.yaml` options:

```yaml
options:
  data_path: "/media/usbdisk/github-runner"
  targets:
    - name: "home-assistant-addons"
      scope: "repo"
      url: "mattbsea/home-assistant-addons"
      token: ""
      labels: "self-hosted,docker"

schema:
  data_path: str
  targets:
    - name: str
      scope: list(repo|org)
      url: str
      token: password
      labels: str?
```

Each target entry carries its **own** PAT rather than sharing one global
token, because a GitHub fine-grained PAT is scoped to either a specific
set of repos or a single org — never a mix — so a shared token can't
cover both a personal repo and an org target at once.

## Authentication / Registration

- User supplies a fine-grained Personal Access Token per target, scoped
  to `Administration: write` (repo-level) or the org "Manage runners"
  permission (org-level).
- Registration tokens returned by GitHub's API expire after one hour, so
  the add-on fetches a fresh one itself at registration time rather than
  asking the user to paste one manually:
  - Repo target: `POST /repos/{owner}/{repo}/actions/runners/registration-token`
  - Org target: `POST /orgs/{org}/actions/runners/registration-token`
- `config.sh --url ... --token ... --labels ... --unattended --replace`
  is run once per target at startup; `--replace` lets re-registration
  after an add-on restart succeed without manual cleanup on GitHub's side.

## Components

- **Dockerfile** — bakes in Docker Engine and the official
  `actions/runner` release tarball at build time (same pattern as this
  repo's `fleet-telemetry` add-on baking in the upstream binary).
- **run.sh** — starts `dockerd` against `${data_path}/docker`, waits for
  the socket, then for each target: mints a registration token, runs
  `config.sh`, and backgrounds that target's `run.sh`. A supervisor loop
  restarts any runner process that exits unexpectedly and re-registers it
  if GitHub has since expired its session.
- **status-server** — small script serving the ingress status page;
  polls `GET /repos|orgs/.../actions/runners` per target every ~30s and
  renders each runner's online/idle/busy state and last-seen time.

## Error Handling

- Invalid/missing PAT for a target → that target's registration fails
  with a clear `bashio::log.error`; other targets keep running
  independently — one bad target must not take down the whole add-on.
- `data_path` missing or not writable at startup → fail fast with a log
  message pointing at Supervisor → Storage → Mounts, rather than
  silently falling back to the container's ephemeral disk (which would
  defeat the purpose of the USB mount).
- Runner process crash → supervisor loop restarts and re-registers it.

## Testing Plan

- Local build via `podman build --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.19 -t local/github-runner ./github-runner`, run with a real USB-backed path bind-mounted and a fine-grained PAT for a throwaway/test repo.
- Verify the runner shows "Idle" under the test repo's Settings → Actions → Runners within ~30s of container start.
- Trigger a workflow with `runs-on: [self-hosted, docker]` doing a trivial `docker build`; confirm it completes and layers land under `<data_path>/docker`.
- Kill a runner process mid-job; confirm the supervisor loop restarts and re-registers it.
- Restart the whole add-on; confirm the Docker build cache on the USB disk survives (a repeat identical build is fast).

## Out of Scope (v1)

- Ephemeral runner mode.
- Per-target isolated Docker daemons.
- GitHub App-based authentication (PAT is sufficient for a single-user setup).
- Non-container (pure toolchain) build support — the add-on's only
  confirmed workload today is Docker/Podman image builds.
