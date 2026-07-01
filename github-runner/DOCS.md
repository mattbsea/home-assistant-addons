# GitHub Actions Runner Add-on Documentation

Runs one or more self-hosted [GitHub Actions](https://docs.github.com/en/actions/hosting-your-own-runners) runners with their own Docker-in-Docker engine, so container-image build workflows that run too long for GitHub-hosted runners can run on your own hardware instead.

## Prerequisite: USB disk mounted as a Supervisor Mount

Docker's image/layer cache and every job's checkout directory need real local disk, not the small HAOS system partition. Attach a USB disk to the Home Assistant host and add it under **Settings → System → Storage → Add Mount** with usage `media`, mount type `Local` (not a network share — Docker's storage driver requires a real local filesystem). Once added, it appears inside this add-on at `/media/<your-disk-name>`.

## Configuration Options

### `data_path`

Where Docker's data and every runner's registration/job-checkout state live. Defaults to `/media/usbdisk/github-runner` — change the `usbdisk` segment to match whatever name you gave the mount above.

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

One shared Docker daemon (rooted at `<data_path>/docker`) serves every configured target's runner process, so build caches are shared across targets. Runners are persistent — they stay registered and keep their `_work` directory across add-on restarts, so a repeat build is fast.

## Troubleshooting

- **"data_path is not writable" at startup** — the USB mount either isn't attached, isn't configured as a Supervisor Mount, or this add-on's `media` mapping isn't enabled. Check Settings → System → Storage.
- **A target never shows "Idle" on GitHub** — check the add-on log for `registration failed`; this almost always means the PAT lacks the right permission for that target's scope (see above).
- **Runner restarts constantly** — check the add-on log around the `restarting` line; the job it was mid-way through when killed will show as failed on GitHub's Actions tab for that repo.

## Known Issue (as of 0.1.7): no job can actually run a container yet

The runner registers, connects to GitHub, and picks up jobs correctly. `dockerd` itself starts
fine. But **every** `docker run`/`docker build` inside a job fails identically:

```
docker: Error response from daemon: failed to create task for container: failed to create
shim task: OCI runtime create failed: runc create failed: unable to start container process:
can't get final child's PID from pipe: EOF
```

This was tested and ruled out across every dimension that normally explains this error, all with
the *same* failure:

- Storage driver: `overlay2` and `vfs`
- Cgroup driver: auto-detected and explicit `cgroupfs`
- Docker/runc version: Debian's `docker.io` (20.10.24 / runc 1.1.5) **and** the current upstream
  `docker-ce` (29.6.1 / runc 1.3.6)
- Security confinement: default, and both `seccomp=unconfined` + `apparmor=unconfined` explicitly
- Capabilities: `privileged: [SYS_ADMIN, NET_ADMIN]` alone, `full_access: true` alone, and both
  together
- Cgroup v2 "no internal process" constraint: confirmed present (root cgroup has live processes
  alongside a partially-enabled `subtree_control`) but moving processes into a child cgroup and
  enabling the remaining controllers live did **not** fix it either
- `dmesg` on the host immediately after a failure shows normal veth/bridge network teardown, no
  audit/seccomp/OOM denial logged

One concrete, confirmed fact from this investigation: **`full_access: true` on this Supervisor
does not set Docker's actual `--privileged` flag.** Inspecting the running container directly
showed `Privileged: false` with capabilities coming only from the explicit `privileged:` list —
`full_access` only lifts seccomp/AppArmor confinement. There is no `config.yaml` key that grants
a literal `--privileged` container on this Supervisor.

**Working theory:** this may be a hard platform-level constraint of HAOS's add-on container
runtime that ordinary Docker-in-Docker cannot work around, regardless of daemon flags.

**Two paths forward, not yet decided:**

1. **Mount the Supervisor's own Docker socket** (`docker_api: true`) instead of running a nested
   daemon — this was considered and rejected during design specifically for isolation (a
   compromised job would get control of every container on the host, not just its own sandbox).
   That isolation argument is weaker now that `full_access: true` was already required just to get
   this far — a security review already flagged it as a high-severity broad grant. This option
   would *definitely* work (the host daemon runs 60+ other containers today).
2. **Keep digging** — `dockerd --debug` in the foreground, or a from-scratch minimal repro outside
   this add-on's own image, to get the actual root cause instead of the pipe-EOF symptom.

Left in a safe resting state: `targets: []`, no credentials configured, add-on running (dockerd +
status page healthy) but not yet useful for real builds.
