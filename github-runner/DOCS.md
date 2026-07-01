# GitHub Actions Runner Add-on Documentation

Runs one or more self-hosted [GitHub Actions](https://docs.github.com/en/actions/hosting-your-own-runners) runners, so container-image build workflows that run too long for GitHub-hosted runners can run on your own hardware instead.

**Security note:** builds run against the Supervisor's own Docker socket (`docker_api: true`), not
an isolated nested daemon — nested Docker-in-Docker could not create any container on this
Supervisor after exhausting every configuration tried (see CHANGELOG 0.1.1-0.1.7 and "Why not
Docker-in-Docker?" below). This means **any workflow this runner executes has control over every
other container on this host**, not just its own sandbox. Only point this at repos/orgs whose
workflow files you trust completely.

## Prerequisite: USB disk mounted as a Supervisor Mount

Docker's image/layer cache and every job's checkout directory need real local disk, not the small HAOS system partition. Attach a USB disk to the Home Assistant host and add it under **Settings → System → Storage → Add Mount** with usage `media`, mount type `Local` (not a network share — Docker's storage driver requires a real local filesystem). Once added, it appears inside this add-on at `/media/<your-disk-name>`.

## Configuration Options

### `data_path`

Where every runner's registration/job-checkout state lives. Defaults to `/media/usbdisk/github-runner` — change the `usbdisk` segment to match whatever name you gave the mount above. (Docker's own image/layer cache lives with the Supervisor's shared Docker installation now, not here — see "Why not Docker-in-Docker?" below.)

### `targets`

A list of repos and/or orgs to run a runner for. Each entry:

| Field    | Description |
|----------|-------------|
| `name`   | A short identifier — becomes part of the runner's GitHub-visible name (`ha-<name>`) |
| `scope`  | `repo` or `org` |
| `url`    | `owner/repo` (repo scope) or `org-name` (org scope) |
| `token`  | A fine-grained Personal Access Token — see below |
| `labels` | Extra comma-separated runner labels beyond the defaults (`self-hosted,linux,x64,docker`) |

### Creating the PAT for a target

1. GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token.
2. **Repo scope:** select the specific repository, then under Repository permissions grant **Administration: Read and write**.
3. **Org scope:** select the organization, then grant the **Self-hosted runners** organization permission (Read and write).
4. Paste the generated token into that target's `token` field.

A single PAT can't cover both a personal repo and an org at once — fine-grained tokens are scoped to one or the other. Give each target its own token.

## Architecture

Every configured target gets its own runner process, but there is no local Docker daemon in this
add-on — `docker build`/`docker run` inside a job go straight to the Supervisor's own Docker
socket (mounted in via `docker_api: true`), the same daemon already running every other add-on and
container on the host. Runners are persistent — they stay registered and keep their `_work`
directory across add-on restarts, so a repeat build is fast. Image/layer caching is whatever the
Supervisor's own Docker installation already does; this add-on doesn't manage it.

## Troubleshooting

- **"data_path is not writable" at startup** — the USB mount either isn't attached, isn't configured as a Supervisor Mount, or this add-on's `media` mapping isn't enabled. Check Settings → System → Storage.
- **"Cannot reach the Docker socket" at startup** — confirm `docker_api: true` is set in this add-on's config (it should be, out of the box).
- **A target never shows "Idle" on GitHub** — check the add-on log for `registration failed`; this almost always means the PAT lacks the right permission for that target's scope (see above).
- **Runner restarts constantly** — check the add-on log around the `restarting` line; the job it was mid-way through when killed will show as failed on GitHub's Actions tab for that repo.

## Why not Docker-in-Docker?

0.1.x tried running an isolated Docker daemon inside this add-on's own container (so a job could
never reach any other container on the host). It never worked: every `docker build`/`docker run`
inside a job failed identically with `unable to start container process: can't get final child's
PID from pipe: EOF`, regardless of:

- Storage driver (`overlay2`, `vfs`)
- Cgroup driver (auto-detected, explicit `cgroupfs`)
- Docker/runc version (Debian's `docker.io` 20.10.24/runc 1.1.5, and current upstream `docker-ce`
  29.6.1/runc 1.3.6)
- Security confinement (default, and both seccomp + AppArmor explicitly unconfined)
- Capabilities (`privileged: [SYS_ADMIN, NET_ADMIN]` alone, `full_access: true` alone, both together)
- The cgroup v2 "no internal process" constraint (confirmed present, live-fixed, no change)

One confirmed fact from that investigation: **`full_access: true` on this Supervisor does not set
Docker's actual `--privileged` flag** — inspecting the running container showed `Privileged:
false`, capabilities coming only from the explicit `privileged:` list. There appears to be no
`config.yaml` key that grants a truly `--privileged` container here, which is very likely why
nested Docker-in-Docker can't work on this Supervisor at all. `docker_api: true` (the host socket)
was adopted as the working alternative, accepting the isolation tradeoff described at the top of
this document.
