# GitHub Actions Runner Add-on Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a new Home Assistant add-on, `github-runner`, that runs one or more persistent self-hosted GitHub Actions runners with their own Docker-in-Docker engine, so long container-image builds can run on local hardware instead of GitHub-hosted runners.

**Architecture:** One privileged add-on container runs a single shared `dockerd` plus one registered runner process per user-configured target (repo or org). All build cache, Docker layers, and job checkout directories live on a Supervisor-mounted USB disk (`/media/usbdisk` inside the container) so they don't compete with the HAOS system partition. A lightweight Python ingress page polls GitHub's API to show each target's online/idle/busy state.

**Tech Stack:** Debian (`amd64-base-debian:bookworm`) + `docker.io` (Docker Engine) + official `actions/runner` release tarball + bash (`run.sh`, bashio) + Python 3 stdlib (status page) + `uv`/`pytest` for the status page's tests.

## Global Constraints

- amd64 only — no aarch64/armv7 support (spec: host is amd64-only; nested DinD is heavier and untested on ARM here).
- Add-on directory: `github-runner/`, slug `github_runner`, following this repo's existing per-add-on layout (`config.yaml`, `build.yaml`, `Dockerfile`, `run.sh`, `CHANGELOG.md`, `DOCS.md`).
- `run.sh` starts with `#!/usr/bin/with-contenv bashio` and must immediately `set +e +u +E +o pipefail` — bashio turns strict mode on by default, and a single benign non-zero check (missing file on fresh install, etc.) silently kills the whole script before its own error handling runs (documented gotcha from this repo's `fleet-telemetry` add-on).
- Runners are **persistent** (not ephemeral) and share **one** Docker daemon across all targets — every target is a repo/org the user personally controls, so there is no isolation requirement between them (per approved spec).
- Storage: all mutable state — Docker's `data-root`, and every runner's registration + `_work` directory — must live under the configurable `data_path` option (default `/media/usbdisk/github-runner`), never under the container's own ephemeral filesystem. Add-on must fail fast at startup if `data_path` is not writable, rather than silently falling back to ephemeral storage.
- `config.yaml` requires `privileged: [SYS_ADMIN, NET_ADMIN]` and `apparmor: false` (`SYS_ADMIN` for the nested Docker daemon's mount/overlay operations, `NET_ADMIN` for its default bridge network's iptables/NAT rules — without it, `dockerd` starts but outbound network access from inside built images/containers silently fails) and `map: [media:rw]` (surfaces the Supervisor `media` mount at `/media/usbdisk` inside the container).
- Python code in this repo uses `uv` for dependency/test management (`pyproject.toml` + `uv.lock`, `uv run pytest`) even though the Dockerfile installs runtime deps separately — this add-on's status page has zero runtime dependencies (stdlib only), so `uv` here is purely for the `pytest` dev dependency.
- GitHub Actions runner version pinned to the current release, **v2.335.1**, with its officially published SHA-256 checksum (`4ef2f25285f0ae4477f1fe1e346db76d2f3ebf03824e2ddd1973a2819bf6c8cf` for `actions-runner-linux-x64-2.335.1.tar.gz`) verified at build time — never download-and-trust without checking this.
- Bash orchestration (`run.sh`, `register-target.sh`) is verified via manual/integration testing against a real disposable GitHub repo, matching this repo's existing convention — no bash unit-test framework exists anywhere in this repo (`fleet-telemetry`/`teslausb-viewer` only unit-test their Python business logic, never their `run.sh`).

---

### Task 1: Add-on scaffold + Docker-in-Docker core

**Files:**
- Create: `github-runner/config.yaml`
- Create: `github-runner/build.yaml`
- Create: `github-runner/Dockerfile`
- Create: `github-runner/run.sh`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: a bootable add-on image whose `run.sh` starts a Docker daemon rooted at `<data_path>/docker` and fails fast if `data_path` isn't writable. Later tasks source this same `run.sh`'s `DATA_PATH`/`DOCKER_DATA_ROOT` variables and append to the same startup sequence.

- [ ] **Step 1: Create `github-runner/config.yaml`**

```yaml
---
name: "GitHub Actions Runner"
description: "Self-hosted GitHub Actions runner(s) for long-running container builds, backed by USB storage"
version: "0.1.0"
slug: "github_runner"
init: false
url: "https://github.com/mattbsea/home-assistant-addons"

arch:
  - amd64

startup: application
boot: auto

privileged:
  - SYS_ADMIN
  - NET_ADMIN
apparmor: false

map:
  - media:rw

options:
  data_path: "/media/usbdisk/github-runner"
  targets: []

schema:
  data_path: str
  targets:
    - name: str
      scope: list(repo|org)
      url: str
      token: password
      labels: str?
```

Note: `targets` defaults to an empty list here — Task 3 wires the run.sh logic that reads it; this task only needs `data_path` to exist so `run.sh` can start Docker.

- [ ] **Step 2: Create `github-runner/build.yaml`**

```yaml
build_from:
  amd64: ghcr.io/home-assistant/amd64-base-debian:bookworm

labels:
  org.opencontainers.image.title: "Home Assistant Add-on: GitHub Actions Runner"
  org.opencontainers.image.description: "Self-hosted GitHub Actions runner(s) for long-running container builds, backed by USB storage"
  org.opencontainers.image.source: "https://github.com/actions/runner"
  org.opencontainers.image.licenses: "MIT"
args:
  RUNNER_VERSION: "2.335.1"
  RUNNER_SHA256: "4ef2f25285f0ae4477f1fe1e346db76d2f3ebf03824e2ddd1973a2819bf6c8cf"
```

- [ ] **Step 3: Create `github-runner/Dockerfile`**

```dockerfile
ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG RUNNER_VERSION=2.335.1
ARG RUNNER_SHA256=4ef2f25285f0ae4477f1fe1e346db76d2f3ebf03824e2ddd1973a2819bf6c8cf

# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        jq \
        git \
        docker.io \
    && rm -rf /var/lib/apt/lists/*

# Download and stage the official GitHub Actions runner release, checksum-verified against the
# value GitHub publishes on the release page. Extracted once here into /opt/actions-runner;
# register-target.sh (Task 2) copies this into each target's own persistent work directory,
# since config.sh writes target-specific registration state into the directory it's run from.
RUN mkdir -p /opt/actions-runner \
    && cd /opt/actions-runner \
    && curl -fsSL -o runner.tar.gz \
        "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz" \
    && echo "${RUNNER_SHA256}  runner.tar.gz" | sha256sum -c - \
    && tar xzf runner.tar.gz \
    && rm runner.tar.gz \
    && ./bin/installdependencies.sh

COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
```

- [ ] **Step 4: Create `github-runner/run.sh` (Docker-in-Docker startup only)**

```bash
#!/usr/bin/with-contenv bashio
# The bashio interpreter enables `set -o errexit errtrace nounset pipefail`. This script turns
# them off deliberately: a single benign non-zero check (e.g. a file that doesn't exist yet on a
# fresh install) would otherwise silently kill the script before its own error handling runs —
# the same bug documented in this repo's fleet-telemetry add-on.
set +e +u +E +o pipefail

DATA_PATH="$(bashio::config 'data_path')"
DOCKER_DATA_ROOT="${DATA_PATH}/docker"
RUNNERS_DIR="${DATA_PATH}/runners"

bashio::log.info "Starting GitHub Runner add-on (data_path=${DATA_PATH})…"

# Fail fast if the USB mount isn't actually there — silently falling back to the container's own
# ephemeral disk would defeat the entire point of the mount (and lose the build cache on restart).
if ! mkdir -p "${DATA_PATH}" 2>/dev/null || [ ! -w "${DATA_PATH}" ]; then
    bashio::log.error "data_path '${DATA_PATH}' is not writable."
    bashio::log.error "Check Settings → System → Storage → Mounts, and that this add-on's" \
        "'media' mapping is enabled."
    exit 1
fi
mkdir -p "${DOCKER_DATA_ROOT}" "${RUNNERS_DIR}"

# --- Start the Docker daemon (Docker-in-Docker) ------------------------------------------------
dockerd --data-root "${DOCKER_DATA_ROOT}" --host=unix:///var/run/docker.sock \
    > "${DATA_PATH}/dockerd.log" 2>&1 &
DOCKERD_PID=$!

bashio::log.info "Waiting for the Docker daemon to become ready…"
for _ in $(seq 1 60); do
    docker info >/dev/null 2>&1 && break
    sleep 1
done
if ! docker info >/dev/null 2>&1; then
    bashio::log.error "Docker daemon did not become ready — see ${DATA_PATH}/dockerd.log"
    exit 1
fi
bashio::log.info "Docker daemon ready (data-root=${DOCKER_DATA_ROOT})."

shutdown() {
    bashio::log.info "Shutting down…"
    kill "${DOCKERD_PID}" 2>/dev/null
    exit 0
}
trap shutdown TERM INT

# Task 2 replaces this with the real target-registration + supervision loop.
wait "${DOCKERD_PID}"
```

- [ ] **Step 5: Build and verify Docker-in-Docker works**

```bash
podman build --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm \
    --build-arg RUNNER_VERSION=2.335.1 \
    --build-arg RUNNER_SHA256=4ef2f25285f0ae4477f1fe1e346db76d2f3ebf03824e2ddd1973a2819bf6c8cf \
    -t local/github-runner ./github-runner
mkdir -p /tmp/github-runner-test/media/usbdisk
cat > /tmp/github-runner-test/options.json <<'EOF'
{"data_path": "/media/usbdisk/github-runner", "targets": []}
EOF
podman run -d --name test-github-runner --privileged \
    -v /tmp/github-runner-test/options.json:/data/options.json:ro \
    -v /tmp/github-runner-test/media:/media \
    local/github-runner
sleep 8
podman exec test-github-runner docker info | grep "Docker Root Dir"
```

Expected: `Docker Root Dir: /media/usbdisk/github-runner/docker`

- [ ] **Step 6: Verify the fail-fast path**

```bash
podman stop test-github-runner && podman rm test-github-runner
cat > /tmp/github-runner-test/options-bad.json <<'EOF'
{"data_path": "/media/usbdisk/does-not-exist/nested", "targets": []}
EOF
podman run --rm --name test-github-runner-fail --privileged \
    -v /tmp/github-runner-test/options-bad.json:/data/options.json:ro \
    local/github-runner 2>&1 | grep "is not writable" || true
```

Expected: since `mkdir -p` on a nonexistent nested path under a real writable parent will actually succeed (that's fine — the fail-fast case that matters is a read-only mount). Re-run with the mount itself read-only instead to confirm the check:

```bash
podman run --rm --name test-github-runner-fail --privileged \
    -v /tmp/github-runner-test/options.json:/data/options.json:ro \
    -v /tmp/github-runner-test/media:/media:ro \
    local/github-runner 2>&1 | grep "is not writable"
```

Expected output contains: `data_path '/media/usbdisk/github-runner' is not writable.`

- [ ] **Step 7: Commit**

```bash
git add github-runner/config.yaml github-runner/build.yaml github-runner/Dockerfile github-runner/run.sh
git commit -m "github-runner: add-on scaffold with Docker-in-Docker core"
```

---

### Task 2: Target registration & multi-target supervision

**Files:**
- Create: `github-runner/scripts/register-target.sh`
- Modify: `github-runner/config.yaml` (replace `targets: []` example with a real single-entry example, keep schema as-is)
- Modify: `github-runner/Dockerfile` (copy `scripts/`)
- Modify: `github-runner/run.sh` (append target registration + supervision loop after Task 1's `wait "${DOCKERD_PID}"` line, which this task removes)

**Interfaces:**
- Consumes: `DATA_PATH`, `RUNNERS_DIR`, `DOCKERD_PID`, `shutdown()`/`trap` from Task 1's `run.sh`.
- Produces: for each configured target, a running runner process under `${RUNNERS_DIR}/<name>/runner`, tracked in the `RUNNER_PIDS`/`RUNNER_DIRS` associative arrays that Task 4 (status wiring) is not required to touch but must not collide with (it introduces its own `STATUS_PID` variable name).

- [ ] **Step 1: Create `github-runner/scripts/register-target.sh`**

```bash
#!/usr/bin/env bash
# Registers (if not already registered) one GitHub Actions runner for a single target
# (repo or org). Idempotent: if this target's runner already has local credentials
# (${runner_home}/.runner), it skips re-registration entirely rather than minting a fresh
# GitHub API token on every restart. Does not start run.sh — the caller does that.
#
# Usage: register-target.sh <name> <scope: repo|org> <url> <token> <labels> <work_dir>
set -u

name="$1"; scope="$2"; url="$3"; token="$4"; labels="$5"; work_dir="$6"

runner_home="${work_dir}/runner"

if [ -f "${runner_home}/.runner" ]; then
    echo "[${name}] already registered (found ${runner_home}/.runner) — skipping re-registration"
    exit 0
fi

api_base="https://api.github.com"
case "${scope}" in
    repo) reg_token_url="${api_base}/repos/${url}/actions/runners/registration-token" ;;
    org)  reg_token_url="${api_base}/orgs/${url}/actions/runners/registration-token" ;;
    *)
        echo "[${name}] unknown scope '${scope}' (expected 'repo' or 'org')" >&2
        exit 1
        ;;
esac

reg_token="$(curl -sf -X POST \
    -H "Authorization: Bearer ${token}" \
    -H "Accept: application/vnd.github+json" \
    "${reg_token_url}" | jq -r '.token // empty')"

if [ -z "${reg_token}" ]; then
    echo "[${name}] failed to obtain a registration token from ${reg_token_url} — check the PAT's scope/permissions" >&2
    exit 1
fi

mkdir -p "${runner_home}"
cp -r /opt/actions-runner/. "${runner_home}/"

export RUNNER_ALLOW_RUNASROOT="1"
( cd "${runner_home}" && ./config.sh \
    --url "https://github.com/${url}" \
    --token "${reg_token}" \
    --name "ha-${name}" \
    --labels "self-hosted,linux,x64,docker${labels:+,${labels}}" \
    --work "${work_dir}/_work" \
    --unattended \
    --replace )
```

- [ ] **Step 2: Update `github-runner/config.yaml`'s example `targets` option**

Replace:

```yaml
options:
  data_path: "/media/usbdisk/github-runner"
  targets: []
```

With:

```yaml
options:
  data_path: "/media/usbdisk/github-runner"
  targets:
    - name: "example"
      scope: "repo"
      url: "owner/repo"
      token: ""
      labels: ""
```

- [ ] **Step 3: Update `github-runner/Dockerfile`** to copy the new scripts directory

Add immediately before the existing `COPY run.sh /run.sh` line:

```dockerfile
COPY scripts/ /opt/scripts/
```

And change the `chmod` line from:

```dockerfile
RUN chmod +x /run.sh
```

to:

```dockerfile
RUN chmod +x /run.sh /opt/scripts/*.sh
```

- [ ] **Step 4: Replace the end of `github-runner/run.sh`**

Remove this line (the last line from Task 1):

```bash
# Task 2 replaces this with the real target-registration + supervision loop.
wait "${DOCKERD_PID}"
```

Replace it with:

```bash
# --- Register + launch one runner process per configured target -------------------------------
declare -A RUNNER_PIDS

start_target() {
    local name="$1" scope="$2" url="$3" token="$4" labels="$5"
    local work_dir="${RUNNERS_DIR}/${name}"

    if ! /opt/scripts/register-target.sh "${name}" "${scope}" "${url}" "${token}" "${labels}" "${work_dir}"; then
        bashio::log.error "[${name}] registration failed — this target will not run. Other targets are unaffected."
        return 1
    fi

    export RUNNER_ALLOW_RUNASROOT="1"
    ( cd "${work_dir}/runner" && exec ./run.sh ) &
    RUNNER_PIDS["${name}"]=$!
    bashio::log.info "[${name}] runner started (pid ${RUNNER_PIDS[${name}]})"
}

target_field() {  # $1 = compact JSON target line, $2 = jq field expression
    echo "$1" | jq -r "$2"
}

mapfile -t TARGET_LINES < <(bashio::config 'targets')
if [ "${#TARGET_LINES[@]}" -eq 0 ]; then
    bashio::log.warning "No targets configured. Add at least one entry under 'targets' in the add-on configuration."
fi

for line in "${TARGET_LINES[@]}"; do
    start_target \
        "$(target_field "${line}" '.name')" \
        "$(target_field "${line}" '.scope')" \
        "$(target_field "${line}" '.url')" \
        "$(target_field "${line}" '.token')" \
        "$(target_field "${line}" '.labels // ""')"
done

shutdown() {
    bashio::log.info "Shutting down…"
    for name in "${!RUNNER_PIDS[@]}"; do kill "${RUNNER_PIDS[${name}]}" 2>/dev/null; done
    kill "${DOCKERD_PID}" 2>/dev/null
    exit 0
}
trap shutdown TERM INT

# --- Supervision loop: restart any runner process that dies, or the whole add-on if dockerd dies
while true; do
    sleep 10
    for name in "${!RUNNER_PIDS[@]}"; do
        pid="${RUNNER_PIDS[${name}]}"
        if ! kill -0 "${pid}" 2>/dev/null; then
            bashio::log.warning "[${name}] runner process exited; restarting."
            line="$(bashio::config 'targets' | jq -c --arg n "${name}" 'select(.name == $n)')"
            start_target \
                "${name}" \
                "$(target_field "${line}" '.scope')" \
                "$(target_field "${line}" '.url')" \
                "$(target_field "${line}" '.token')" \
                "$(target_field "${line}" '.labels // ""')"
        fi
    done
    if ! kill -0 "${DOCKERD_PID}" 2>/dev/null; then
        bashio::log.error "Docker daemon died — exiting so the Supervisor restarts the add-on."
        exit 1
    fi
done
```

- [ ] **Step 5: Manual integration test against a real disposable repo**

This step requires a real, throwaway GitHub repo you control and a fine-grained PAT scoped to it (`Administration: write` permission) — bash orchestration in this repo is always integration-tested this way, never unit-tested (see Global Constraints).

```bash
podman build --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm \
    --build-arg RUNNER_VERSION=2.335.1 \
    --build-arg RUNNER_SHA256=4ef2f25285f0ae4477f1fe1e346db76d2f3ebf03824e2ddd1973a2819bf6c8cf \
    -t local/github-runner ./github-runner

mkdir -p /tmp/github-runner-test/media/usbdisk
cat > /tmp/github-runner-test/options.json <<EOF
{
  "data_path": "/media/usbdisk/github-runner",
  "targets": [
    {"name": "test", "scope": "repo", "url": "YOUR_USER/YOUR_TEST_REPO", "token": "YOUR_PAT", "labels": ""}
  ]
}
EOF

podman run -d --name test-github-runner --privileged \
    -v /tmp/github-runner-test/options.json:/data/options.json:ro \
    -v /tmp/github-runner-test/media:/media \
    local/github-runner
sleep 15
podman logs test-github-runner | grep "runner started"
```

Expected: log line `[test] runner started (pid ...)`, and the runner shows as **Idle** under `https://github.com/YOUR_USER/YOUR_TEST_REPO/settings/actions/runners`.

Then kill the runner process to confirm the supervision loop restarts it:

```bash
podman exec test-github-runner pkill -f Runner.Listener
sleep 15
podman logs test-github-runner | grep "restarting"
```

Expected: log line `[test] runner process exited; restarting.` followed by `[test] runner started (pid ...)`.

- [ ] **Step 6: Commit**

```bash
git add github-runner/scripts/register-target.sh github-runner/config.yaml github-runner/Dockerfile github-runner/run.sh
git commit -m "github-runner: register and supervise one runner process per target"
```

---

### Task 3: Ingress status page (TDD)

**Files:**
- Create: `github-runner/pyproject.toml`
- Create: `github-runner/status_server/server.py`
- Create: `github-runner/tests/test_server.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure Python, no bash dependency).
- Produces: `main()` in `status_server/server.py`, which Task 4 invokes from `run.sh` via `python3 /opt/status_server/server.py`, reading the `GH_STATUS_TARGETS` environment variable (a JSON array of `{name, scope, url, token}` — same shape as the `targets` config option) and serving HTML on port `8099`.

- [ ] **Step 1: Create `github-runner/pyproject.toml`**

```toml
[project]
name = "github-runner-addon"
version = "0.1.0"
description = "GitHub Actions self-hosted runner Home Assistant add-on — status page"
requires-python = ">=3.11"
dependencies = []

[dependency-groups]
dev = [
    "pytest>=8",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

- [ ] **Step 2: Write the failing tests — `github-runner/tests/test_server.py`**

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "status_server"))

from server import api_url_for, summarize_target, render_html


def test_api_url_for_repo_scope():
    target = {"scope": "repo", "url": "mattbsea/home-assistant-addons"}
    assert api_url_for(target) == (
        "https://api.github.com/repos/mattbsea/home-assistant-addons/actions/runners"
    )


def test_api_url_for_org_scope():
    target = {"scope": "org", "url": "my-org"}
    assert api_url_for(target) == "https://api.github.com/orgs/my-org/actions/runners"


def test_summarize_target_unknown_on_fetch_failure():
    target = {"name": "addons", "url": "mattbsea/home-assistant-addons", "scope": "repo"}
    row = summarize_target(target, None)
    assert row["state"] == "unknown"


def test_summarize_target_not_registered_when_no_match():
    target = {"name": "addons", "url": "mattbsea/home-assistant-addons", "scope": "repo"}
    row = summarize_target(target, runners=[])
    assert row["state"] == "not registered"


def test_summarize_target_busy():
    target = {"name": "addons", "url": "mattbsea/home-assistant-addons", "scope": "repo"}
    runners = [{"name": "ha-addons", "status": "online", "busy": True, "labels": [{"name": "docker"}]}]
    row = summarize_target(target, runners)
    assert row["state"] == "busy"
    assert "docker" in row["detail"]


def test_summarize_target_online_idle():
    target = {"name": "addons", "url": "mattbsea/home-assistant-addons", "scope": "repo"}
    runners = [{"name": "ha-addons", "status": "online", "busy": False, "labels": []}]
    row = summarize_target(target, runners)
    assert row["state"] == "online"


def test_render_html_includes_target_name_and_state():
    rows = [{"name": "addons", "url": "mattbsea/home-assistant-addons", "scope": "repo",
             "state": "online", "detail": "labels: docker"}]
    html = render_html(rows)
    assert "addons" in html
    assert "online" in html
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
cd github-runner && uv run pytest tests/test_server.py -v
```

Expected: `ModuleNotFoundError: No module named 'server'` (the file doesn't exist yet).

- [ ] **Step 4: Create `github-runner/status_server/server.py`**

```python
"""Minimal ingress status page for the GitHub Runner add-on.

Polls each configured target's GitHub API runners endpoint on a timer and serves
a small self-contained HTML table showing each runner's online/busy state.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

POLL_SECONDS = 30
LISTEN_PORT = 8099


def api_url_for(target):
    if target["scope"] == "org":
        return f"https://api.github.com/orgs/{target['url']}/actions/runners"
    return f"https://api.github.com/repos/{target['url']}/actions/runners"


def fetch_runners(target, timeout=10):
    """Return the GitHub-reported runners list for this target, or None on failure."""
    req = urllib.request.Request(
        api_url_for(target),
        headers={
            "Authorization": f"Bearer {target['token']}",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
    except (urllib.error.URLError, ValueError, TimeoutError):
        return None
    return body.get("runners", [])


def summarize_target(target, runners):
    """Reduce one target + its GitHub API runners list into a display row.

    `runners` is None when the API call failed, or a (possibly empty) list of
    GitHub's runner objects on success.
    """
    row = {"name": target["name"], "url": target["url"], "scope": target["scope"]}
    if runners is None:
        row["state"] = "unknown"
        row["detail"] = "GitHub API call failed — check the PAT and network"
        return row
    match = next((r for r in runners if r.get("name") == f"ha-{target['name']}"), None)
    if match is None:
        row["state"] = "not registered"
        row["detail"] = "No matching runner name reported by GitHub yet"
        return row
    if match.get("busy"):
        row["state"] = "busy"
    elif match.get("status") == "online":
        row["state"] = "online"
    else:
        row["state"] = "offline"
    labels = ", ".join(l["name"] for l in match.get("labels", []))
    row["detail"] = f"labels: {labels}"
    return row


def render_html(rows):
    """Render the status rows as a small standalone HTML page (no external assets)."""
    state_colors = {
        "online": "#2e7d32",
        "busy": "#ef6c00",
        "offline": "#c62828",
        "not registered": "#757575",
        "unknown": "#757575",
    }
    body_rows = "\n".join(
        f"<tr><td>{r['name']}</td><td>{r['scope']}</td><td>{r['url']}</td>"
        f"<td style='color:{state_colors.get(r['state'], '#000')}'>{r['state']}</td>"
        f"<td>{r['detail']}</td></tr>"
        for r in rows
    )
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>GitHub Runner Status</title>
<style>
body {{ font-family: sans-serif; margin: 1.5rem; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ddd; padding: 0.5rem; text-align: left; }}
th {{ background: #f5f5f5; }}
</style></head>
<body>
<h1>GitHub Runner Status</h1>
<table>
<tr><th>Target</th><th>Scope</th><th>Repo/Org</th><th>State</th><th>Detail</th></tr>
{body_rows}
</table>
</body></html>"""


class StatusCache:
    """Background poller shared by all request handlers."""

    def __init__(self, targets, poll_seconds=POLL_SECONDS):
        self._targets = targets
        self._poll_seconds = poll_seconds
        self._lock = threading.Lock()
        self._html = render_html([summarize_target(t, None) for t in targets])

    def poll_forever(self):
        while True:
            rows = [summarize_target(t, fetch_runners(t)) for t in self._targets]
            with self._lock:
                self._html = render_html(rows)
            time.sleep(self._poll_seconds)

    def html(self):
        with self._lock:
            return self._html


def make_handler(cache):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = cache.html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass  # quiet; the add-on log already carries dockerd/runner output

    return Handler


def main():
    targets = json.loads(os.environ.get("GH_STATUS_TARGETS", "[]"))
    cache = StatusCache(targets)
    threading.Thread(target=cache.poll_forever, daemon=True).start()
    server = HTTPServer(("0.0.0.0", LISTEN_PORT), make_handler(cache))
    server.serve_forever()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd github-runner && uv run pytest tests/test_server.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 6: Commit**

```bash
cd github-runner && uv lock
git add github-runner/pyproject.toml github-runner/uv.lock github-runner/status_server/server.py github-runner/tests/test_server.py
git commit -m "github-runner: add ingress status page with unit-tested status logic"
```

---

### Task 4: Wire the status page into the add-on

**Files:**
- Modify: `github-runner/config.yaml` (add ingress settings)
- Modify: `github-runner/Dockerfile` (copy `status_server/`, install `python3`)
- Modify: `github-runner/run.sh` (start the status server, export `GH_STATUS_TARGETS`, kill it on shutdown)

**Interfaces:**
- Consumes: `bashio::config 'targets'` (same source Task 2 reads), `RUNNER_PIDS`/`DOCKERD_PID`/`shutdown()` from Task 2's `run.sh`.
- Produces: the add-on's ingress endpoint on port 8099, serving the HTML from Task 3's `status_server/server.py`.

- [ ] **Step 1: Update `github-runner/config.yaml`**

Add these keys (after `map:`, before `options:`):

```yaml
ingress: true
ingress_port: 8099
panel_icon: mdi:github
panel_title: "GitHub Runner"
```

- [ ] **Step 2: Update `github-runner/Dockerfile`**

Add `python3` to the apt install list (it's needed at runtime for the status server, not just build time):

```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        jq \
        git \
        docker.io \
        python3 \
    && rm -rf /var/lib/apt/lists/*
```

Add, immediately after the existing `COPY scripts/ /opt/scripts/` line:

```dockerfile
COPY status_server/ /opt/status_server/
```

- [ ] **Step 3: Update `github-runner/run.sh`**

Insert this block immediately after the `for line in "${TARGET_LINES[@]}"; do ... done` loop and before the `shutdown()` function definition:

```bash
# --- Status server (ingress) ---------------------------------------------------------------
export GH_STATUS_TARGETS="$(bashio::config 'targets')"
python3 /opt/status_server/server.py &
STATUS_PID=$!
bashio::log.info "Status page listening on :8099"
```

Replace the existing `shutdown()` function:

```bash
shutdown() {
    bashio::log.info "Shutting down…"
    for name in "${!RUNNER_PIDS[@]}"; do kill "${RUNNER_PIDS[${name}]}" 2>/dev/null; done
    kill "${DOCKERD_PID}" 2>/dev/null
    exit 0
}
```

With:

```bash
shutdown() {
    bashio::log.info "Shutting down…"
    for name in "${!RUNNER_PIDS[@]}"; do kill "${RUNNER_PIDS[${name}]}" 2>/dev/null; done
    kill "${STATUS_PID}" 2>/dev/null
    kill "${DOCKERD_PID}" 2>/dev/null
    exit 0
}
```

- [ ] **Step 4: Build and verify the status page serves real content**

```bash
podman build --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm \
    --build-arg RUNNER_VERSION=2.335.1 \
    --build-arg RUNNER_SHA256=4ef2f25285f0ae4477f1fe1e346db76d2f3ebf03824e2ddd1973a2819bf6c8cf \
    -t local/github-runner ./github-runner

cat > /tmp/github-runner-test/options.json <<'EOF'
{"data_path": "/media/usbdisk/github-runner", "targets": []}
EOF

podman run -d --name test-github-runner --privileged -p 8099:8099 \
    -v /tmp/github-runner-test/options.json:/data/options.json:ro \
    -v /tmp/github-runner-test/media:/media \
    local/github-runner
sleep 8
curl -s http://localhost:8099/ | grep "GitHub Runner Status"
```

Expected: the `<h1>GitHub Runner Status</h1>` heading is present in the response.

- [ ] **Step 5: Commit**

```bash
git add github-runner/config.yaml github-runner/Dockerfile github-runner/run.sh
git commit -m "github-runner: wire ingress status page into the add-on"
```

---

### Task 5: Documentation & full end-to-end verification

**Files:**
- Create: `github-runner/CHANGELOG.md`
- Create: `github-runner/DOCS.md`

**Interfaces:**
- Consumes: the finished add-on from Tasks 1–4.
- Produces: nothing further (terminal task).

- [ ] **Step 1: Create `github-runner/CHANGELOG.md`**

```markdown
## 0.1.0 - 2026-06-30

### Added

- Initial release: self-hosted GitHub Actions runner(s), one per configured repo/org target
- Docker-in-Docker (privileged) so container image build/push workflows work out of the box
- Persistent runners with a shared Docker daemon and build cache on the mounted USB disk (`media:rw`)
- Ingress status page showing each target's online/idle/busy state
```

- [ ] **Step 2: Create `github-runner/DOCS.md`**

```markdown
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
- **Runner restarts constantly** — check `podman logs`/add-on log around the `restarting` line; the job it was mid-way through when killed will show as failed on GitHub's Actions tab for that repo.
```

- [ ] **Step 3: Full end-to-end verification against a real disposable test repo**

```bash
podman build --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm \
    --build-arg RUNNER_VERSION=2.335.1 \
    --build-arg RUNNER_SHA256=4ef2f25285f0ae4477f1fe1e346db76d2f3ebf03824e2ddd1973a2819bf6c8cf \
    -t local/github-runner ./github-runner

mkdir -p /tmp/github-runner-e2e/media/usbdisk
cat > /tmp/github-runner-e2e/options.json <<EOF
{
  "data_path": "/media/usbdisk/github-runner",
  "targets": [
    {"name": "test", "scope": "repo", "url": "YOUR_USER/YOUR_TEST_REPO", "token": "YOUR_PAT", "labels": ""}
  ]
}
EOF

podman run -d --name e2e-github-runner --privileged -p 8099:8099 \
    -v /tmp/github-runner-e2e/options.json:/data/options.json:ro \
    -v /tmp/github-runner-e2e/media:/media \
    local/github-runner
sleep 15
```

In `YOUR_USER/YOUR_TEST_REPO`, add `.github/workflows/build-test.yml`:

```yaml
name: build-test
on: workflow_dispatch
jobs:
  build:
    runs-on: [self-hosted, docker]
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t e2e-test - <<'EOF'
          FROM alpine:3.20
          RUN echo "hello from the runner"
        EOF
```

Trigger it via **Actions → build-test → Run workflow**, then confirm:

```bash
# Job completed successfully on GitHub's Actions tab, AND:
podman exec e2e-github-runner du -sh /media/usbdisk/github-runner/docker
```

Expected: a non-trivial size (image layers landed under the USB-backed data root, not the container's ephemeral disk).

Then restart the add-on and re-run the same workflow to confirm the cache survives:

```bash
podman restart e2e-github-runner
sleep 15
# Re-run build-test from GitHub's UI — confirm it completes noticeably faster (cached base layer).
podman logs e2e-github-runner | grep "already registered"
```

Expected: log line `[test] already registered (found ... /.runner) — skipping re-registration`, confirming the runner's persistent state survived the restart.

- [ ] **Step 4: Clean up test containers**

```bash
podman stop test-github-runner e2e-github-runner 2>/dev/null
podman rm test-github-runner e2e-github-runner 2>/dev/null
rm -rf /tmp/github-runner-test /tmp/github-runner-e2e
```

- [ ] **Step 5: Commit**

```bash
git add github-runner/CHANGELOG.md github-runner/DOCS.md
git commit -m "github-runner: add documentation"
```
