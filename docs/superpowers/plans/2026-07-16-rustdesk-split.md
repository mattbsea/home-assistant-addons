# RustDesk Split (server + web-client add-ons) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken `rustdesk` add-on (built on a privileged GUI-desktop image that was never a server) with two purpose-built add-ons: `rustdesk-server` (official `rustdesk/rustdesk-server` hbbs/hbbr binaries, headless) and `rustdesk-web` (`lejianwen/rustdesk-api`, a lightweight web-admin + browser client), each on its own correct upstream base.

**Architecture:** `rustdesk-server` runs hbbs+hbbr on a plain Debian HA base image, no privileges, no ingress, ports 21115-21119 published for native clients. `rustdesk-web` runs `lejianwen/rustdesk-api` pointed at `rustdesk-server`'s internal hostname over HA's docker network, served over ingress on port 21114; its browser JS opens a direct WebSocket to `rustdesk-server`'s 21118/21119 for the actual remote-control data path (documented as needing NPM TLS termination, not automated here — see spec's Out of Scope).

**Tech Stack:** Home Assistant add-on framework (`config.yaml`/`build.yaml`/Dockerfile), bashio (rustdesk-server only — it's on an HA base image), plain POSIX `sh` + `jq` (rustdesk-web — its base is a third-party Alpine image without bashio), HA MCP tools (`ha_manage_addon`, `ha_get_addon`, `ha_get_logs`) for live build/install/verify since this repo's dev sandbox has no local podman/docker.

## Global Constraints

- Design source of truth: `docs/superpowers/specs/2026-07-16-rustdesk-split-design.md`. Every value below (ports, env vars, hostnames) is copied verbatim from it or from live verification against the real upstream images/releases done during spec research.
- Target architectures: `aarch64` and `amd64` only (matches the spec's stated scope; do not add `armv7` even though both upstream projects happen to publish it — that's a deliberate scope decision, not an oversight).
- `rustdesk-server`'s internal HA hostname is `a44b0313-rustdesk-server` (repo slug `a44b0313` + add-on slug `rustdesk_server`, underscores become dashes — confirmed against this repo's existing `a44b0313_fleet_telemetry` → `a44b0313-fleet-telemetry` mapping via `ha_get_addon`). `rustdesk-web`'s is `a44b0313-rustdesk-web`.
- Pinned upstream versions: `rustdesk/rustdesk-server` release `1.1.15` (same pin the old add-on used — its hbbs/hbbr fetch logic is being carried over, not re-derived) — confirmed both `_amd64.deb` and `_arm64.deb` assets exist on that release. `lejianwen/rustdesk-api:v2.7` — confirmed multi-arch manifest covering `amd64`/`arm64` via Docker Hub, pinned instead of `:latest` to match this repo's convention (e.g. fleet-telemetry pins `tesla/fleet-telemetry:v0.9.0`) of deliberate version bumps over silent upstream drift.
- No local container build tooling is available in this working environment (no `podman`/`docker`/`nix` on PATH) — verification happens by pushing to `origin/main` and using the `ha_manage_addon`/`ha_get_addon`/`ha_get_logs` MCP tools against the real Home Assistant instance, the same tools and workflow already used earlier in this session to diagnose the original crash.
- This repo has no automated test suite for add-ons (neither the old `rustdesk` add-on nor `fleet-telemetry`/`teslausb-viewer` have one) — the "test" for each task here is a clean, non-crash-looping live start, verified via `ha_get_logs(source="supervisor", slug=...)`, matching this repo's existing convention.

---

### Task 1: `rustdesk-server` add-on files

**Files:**
- Create: `rustdesk-server/config.yaml`
- Create: `rustdesk-server/build.yaml`
- Create: `rustdesk-server/Dockerfile`
- Create: `rustdesk-server/run.sh`
- Create: `rustdesk-server/CHANGELOG.md`
- Create: `rustdesk-server/DOCS.md`

**Interfaces:**
- Produces: an installable add-on with slug `a44b0313_rustdesk_server`, internal hostname `a44b0313-rustdesk-server`, listening on `21116` (hbbs ID/rendezvous, TCP+UDP), `21117` (hbbr relay, TCP), `21115`/`21118`/`21119` (TCP, hbbs auxiliary + websocket ports). Later tasks (`rustdesk-web`) depend on this hostname and these two port numbers being correct.

- [ ] **Step 1: Write `rustdesk-server/config.yaml`**

```yaml
---
name: "RustDesk Server"
description: "Self-hosted RustDesk relay server (hbbs/hbbr) — headless, no browser client"
version: "1.0.0"
slug: "rustdesk_server"
init: false

arch:
  - aarch64
  - amd64

url: "https://github.com/rustdesk/rustdesk-server"

startup: services
boot: auto

options:
  relay_host: ""
  encrypted_only: true
  custom_key: ""
schema:
  relay_host: "str?"
  encrypted_only: "bool"
  custom_key: "password?"

# The actual RustDesk wire protocol, unrelated to ingress (this add-on has none — it's headless).
# These must be reachable from the internet (via router port-forward) for devices outside your
# LAN to connect, and 21118/21119 must additionally be reachable by any browser running the
# rustdesk-web add-on's client (see that add-on's DOCS.md for the NPM TLS setup that requires).
ports:
  21115/tcp: 21115
  21116/tcp: 21116
  21116/udp: 21116
  21117/tcp: 21117
  21118/tcp: 21118
  21119/tcp: 21119
ports_description:
  21115/tcp: "hbbs: NAT type test"
  21116/tcp: "hbbs: hole punching / connection service"
  21116/udp: "hbbs: ID registration & heartbeat (required)"
  21117/tcp: "hbbr: relay service"
  21118/tcp: "hbbs: web client WebSocket (needs NPM TLS in front for browser use — see rustdesk-web's DOCS.md)"
  21119/tcp: "hbbr: web client WebSocket (needs NPM TLS in front for browser use — see rustdesk-web's DOCS.md)"

# hbbs/hbbr's identity keypair. Losing this changes the server's identity and breaks every
# client that already trusts the old public key.
map:
  - data:rw
```

- [ ] **Step 2: Write `rustdesk-server/build.yaml`**

```yaml
build_from:
  aarch64: ghcr.io/home-assistant/aarch64-base-debian:bookworm
  amd64: ghcr.io/home-assistant/amd64-base-debian:bookworm

labels:
  org.opencontainers.image.title: "Home Assistant Add-on: RustDesk Server"
  org.opencontainers.image.description: "Self-hosted RustDesk relay server (hbbs/hbbr) — headless, no browser client"
  org.opencontainers.image.source: "https://github.com/rustdesk/rustdesk-server"
  org.opencontainers.image.licenses: "AGPL-3.0"
```

- [ ] **Step 3: Write `rustdesk-server/Dockerfile`**

```dockerfile
# BUILD_FROM must be a global ARG (declared before the first FROM) so the Home Assistant
# builder's --build-arg substitution reaches the runtime stage below.
ARG BUILD_FROM
FROM ${BUILD_FROM}

# Pinned upstream rustdesk-server release. Bump deliberately, not automatically.
ARG RUSTDESK_SERVER_VERSION=1.1.15
ARG BUILD_VERSION
ENV RD_ADDON_VERSION=${BUILD_VERSION}

# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

# rustdesk-server ships hbbs/hbbr as .deb packages, one per Debian architecture (amd64/arm64),
# which map 1:1 onto `dpkg --print-architecture` — no translation table needed. Extract with
# dpkg-deb instead of `dpkg -i`: the packages carry systemd unit files and postinst hooks that
# assume a running init system, and this image's init is bashio/s6-less HA base, not systemd.
RUN set -eu; \
    DEB_ARCH="$(dpkg --print-architecture)"; \
    TMP="$(mktemp -d)"; \
    for pkg in hbbs hbbr; do \
        url="https://github.com/rustdesk/rustdesk-server/releases/download/${RUSTDESK_SERVER_VERSION}/rustdesk-server-${pkg}_${RUSTDESK_SERVER_VERSION}_${DEB_ARCH}.deb"; \
        curl -fsSL -o "${TMP}/${pkg}.deb" "${url}"; \
        dpkg-deb -x "${TMP}/${pkg}.deb" "${TMP}/${pkg}"; \
        install -m 0755 "${TMP}/${pkg}/usr/bin/${pkg}" "/usr/local/bin/${pkg}"; \
    done; \
    rm -rf "${TMP}"; \
    test -x /usr/local/bin/hbbs; \
    test -x /usr/local/bin/hbbr

COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
```

- [ ] **Step 4: Write `rustdesk-server/run.sh`**

```bash
#!/usr/bin/with-contenv bashio
# Runs hbbs (ID/rendezvous server) and hbbr (relay server) concurrently, each restarted
# independently if it exits. bashio enables bash strict mode (-e/-u/-o pipefail) on top of what
# this script sets; that turns any transient benign failure into a silent add-on crash, so we
# explicitly turn it back off (see this repo's fleet-telemetry run.sh for the same pattern).
set +e +u +E +o pipefail

mkdir -p /data/rustdesk
cd /data/rustdesk || exit 1

ENCRYPTED_ONLY="$(bashio::config 'encrypted_only')"
CUSTOM_KEY="$(bashio::config 'custom_key')"
RELAY_HOST="$(bashio::config 'relay_host')"

KEY_ARGS=()
if [ -n "${CUSTOM_KEY}" ]; then
    KEY_ARGS=(-k "${CUSTOM_KEY}")
elif [ "${ENCRYPTED_ONLY}" = "true" ]; then
    KEY_ARGS=(-k _)
fi

RELAY_ARGS=()
if [ -n "${RELAY_HOST}" ]; then
    RELAY_ARGS=(-r "${RELAY_HOST}:21117")
else
    bashio::log.warning "relay_host is not set: clients outside your LAN will not reach this server."
    bashio::log.warning "Set relay_host and forward ports 21115-21119 (21116 also UDP) to reach it remotely."
fi

run_supervised() {
    local name="$1"
    shift
    while true; do
        bashio::log.info "Starting ${name}..."
        "$@"
        bashio::log.warning "${name} exited; restarting in 5s"
        sleep 5
    done
}

run_supervised hbbr /usr/local/bin/hbbr "${KEY_ARGS[@]}" &
run_supervised hbbs /usr/local/bin/hbbs "${RELAY_ARGS[@]}" "${KEY_ARGS[@]}" &

wait
```

- [ ] **Step 5: Write `rustdesk-server/CHANGELOG.md`**

```markdown
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
```

- [ ] **Step 6: Write `rustdesk-server/DOCS.md`**

```markdown
# RustDesk Server

Self-hosted [RustDesk](https://rustdesk.com) relay server — the official `hbbs`/`hbbr` binaries,
headless, with no GUI or browser client of its own.

## About

This add-on used to bundle a browser-accessible client too, built on top of
`linuxserver/docker-rustdesk`. That image turned out to be the RustDesk **desktop client** in a
privileged, GPU/input-device-requiring browser-streamed sandbox — not a server, and not safely
runnable without privileges an ingress add-on shouldn't have. It's been split: this add-on is
just the relay server; see the separate **RustDesk Web Client** add-on for browser access.

## Installation

1. Add this repository to your Home Assistant Add-on Store.
2. Install **RustDesk Server**.
3. Set `relay_host` (see Configuration) if you want devices outside your LAN — or the
   **RustDesk Web Client** add-on's browser sessions — to reach this server.
4. Start the add-on.

## Configuration

| Option | Default | Description |
|---|---|---|
| `relay_host` | *(blank)* | Public address clients use to reach `hbbs`/`hbbr`. Blank = LAN only. |
| `encrypted_only` | `true` | Require encrypted connections to the server. |
| `custom_key` | *(blank)* | Optional fixed pre-shared key instead of the auto-generated one. |

## Usage

This add-on has no UI of its own. Point native RustDesk clients (desktop/mobile) at
`relay_host` (ID server port 21116, key from `custom_key` or the server's auto-generated one —
find it by installing the **RustDesk Web Client** add-on and checking its admin panel, or via
this add-on's logs on first boot). For browser access, install **RustDesk Web Client**.

## Data persistence

`/data/rustdesk`: the server's identity keypair (`id_ed25519`/`id_ed25519.pub`). **Do not delete
`/data`** — every client that already trusts the old public key will refuse to connect (or show
a "key mismatch" warning) after the keypair changes.
```

- [ ] **Step 7: Commit**

```bash
git add rustdesk-server/
git commit -m "$(cat <<'EOF'
Add rustdesk-server add-on (headless hbbs/hbbr)

Replaces the hbbs/hbbr half of the broken rustdesk add-on, rehosted on a
plain Debian base instead of linuxserver/docker-rustdesk's privileged GUI
desktop image. No GUI, no privileges, no ingress needed.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Deploy and verify `rustdesk-server` on the live instance

**Files:** none (uses HA MCP tools only)

**Interfaces:**
- Consumes: `rustdesk-server/` files from Task 1, already committed.
- Produces: a running, non-crash-looping `a44b0313_rustdesk_server` add-on on the live HA
  instance — Task 4's `rustdesk-web` verification depends on this being up first.

- [ ] **Step 1: Push to origin/main**

```bash
git push origin main
```

- [ ] **Step 2: Confirm the add-on is visible in the store**

Call `ha_get_addon(source="available", query="rustdesk")`.
Expected: an entry with `"slug":"a44b0313_rustdesk_server"`, `"installed":false`. If it's
missing, the Supervisor store hasn't picked up the push yet — re-check after a minute (it
polled and picked up the previous rustdesk push within ~2 minutes without any manual reload
step earlier in this session).

- [ ] **Step 3: Install**

Call `ha_manage_addon(slug="a44b0313_rustdesk_server", action="install")`.

- [ ] **Step 4: Check the build/install log for errors**

Call `ha_get_logs(source="system_service", slug="supervisor", search="rustdesk_server", hours_back=1, order="newest", limit=100)`.
Expected: a `docker buildx build` command line, ending in `Build ... done` and
`App 'a44b0313_rustdesk_server' successfully installed` — same pattern as the original
add-on's (broken) install, confirming the Dockerfile itself builds cleanly this time on the
new base image.
If the build fails: read the error in this log (most likely cause: the `curl` URLs in the
Dockerfile — re-verify against `gh api repos/rustdesk/rustdesk-server/releases/tags/1.1.15
-q '.assets[].name'` in case the release assets changed).

- [ ] **Step 5: Configure `relay_host` if you have a public domain for this**

If you want native clients to reach this server from outside your LAN, call
`ha_manage_addon(slug="a44b0313_rustdesk_server", options={"relay_host": "<your-domain>"})`
before starting it. Otherwise skip — LAN-only is fine for now and `relay_host` can be set later.

- [ ] **Step 6: Start it**

Call `ha_manage_addon(slug="a44b0313_rustdesk_server", action="start")`.

- [ ] **Step 7: Verify clean startup (no crash loop)**

Call `ha_get_logs(source="supervisor", slug="a44b0313_rustdesk_server", limit=40, order="newest")`.
Expected: two `bashio::log.info "Starting hbbs..."` / `"Starting hbbr..."` lines (once each,
not repeating every few seconds — repetition means one of them is crash-looping) and no
`PermissionDenied`/`cannot load certificate`/`mount: permission denied` noise (those were the
old add-on's symptoms; this image has none of the machinery that produced them, so their
absence here is the actual regression test for the bug this whole plan exists to fix).

- [ ] **Step 8: Confirm the add-on shows as started**

Call `ha_get_addon(slug="a44b0313_rustdesk_server")`. Expected: `"state":"started"`.

---

### Task 3: `rustdesk-web` add-on files

**Files:**
- Create: `rustdesk-web/config.yaml`
- Create: `rustdesk-web/build.yaml`
- Create: `rustdesk-web/Dockerfile`
- Create: `rustdesk-web/run.sh`
- Create: `rustdesk-web/CHANGELOG.md`
- Create: `rustdesk-web/DOCS.md`

**Interfaces:**
- Consumes: `rustdesk-server`'s internal hostname `a44b0313-rustdesk-server` and ports `21116`
  (ID server)/`21117` (relay), fixed in this add-on's `run.sh` — from Task 1/Global Constraints.
- Produces: an installable add-on with slug `a44b0313_rustdesk_web`, ingress on port `21114`.

- [ ] **Step 1: Write `rustdesk-web/config.yaml`**

```yaml
---
name: "RustDesk Web Client"
description: "Browser-accessible RustDesk client (web-admin + web client), served over ingress"
version: "1.0.0"
slug: "rustdesk_web"
init: false

arch:
  - aarch64
  - amd64

url: "https://github.com/lejianwen/rustdesk-api"

startup: application
boot: auto

# Serves the web-admin (/_admin/) and browser RustDesk client. The client's actual
# remote-control data path is a WebSocket the browser opens directly to the rustdesk-server
# add-on's 21118/21119 (NOT proxied through this ingress tunnel) — see DOCS.md for the NPM
# TLS setup that path needs.
ingress: true
ingress_port: 21114
panel_icon: mdi:monitor-share
panel_title: "RustDesk"

# Optional: only needed to reach this add-on's API directly from outside HA ingress (e.g. for
# share links usable by guests without an HA login, or native clients that discover the API
# server directly). Ingress-only browser access works with this left blank.
ports:
  21114/tcp: 21114
ports_description:
  21114/tcp: "API + web-admin (/_admin/) + web client — optional, only if you want this reachable outside HA ingress."

options:
  ws_host: ""
schema:
  ws_host: "str?"

# rustdesk-api's own sqlite db (users, address book, connection/transfer logs).
map:
  - data:rw
```

- [ ] **Step 2: Write `rustdesk-web/build.yaml`**

```yaml
build_from:
  aarch64: lejianwen/rustdesk-api:v2.7
  amd64: lejianwen/rustdesk-api:v2.7

labels:
  org.opencontainers.image.title: "Home Assistant Add-on: RustDesk Web Client"
  org.opencontainers.image.description: "Browser-accessible RustDesk client (web-admin + web client), served over ingress"
  org.opencontainers.image.source: "https://github.com/lejianwen/rustdesk-api"
  org.opencontainers.image.licenses: "MIT"
```

- [ ] **Step 3: Write `rustdesk-web/Dockerfile`**

```dockerfile
# BUILD_FROM must be a global ARG (declared before the first FROM) so the Home Assistant
# builder's --build-arg substitution reaches the runtime stage below. Points at
# lejianwen/rustdesk-api (see build.yaml) — a lightweight web-admin + browser RustDesk client
# that talks to an external hbbs/hbbr server over the env vars run.sh sets, rather than
# bundling its own (the `:full-s6` variant of this image does bundle one; we deliberately don't
# use it, since the rustdesk-server add-on already provides that role).
ARG BUILD_FROM
FROM ${BUILD_FROM}

# jq: run.sh reads Home Assistant's /data/options.json. This base image has no bashio (that's
# specific to Home Assistant's own base images; this one is lejianwen's plain Alpine image), so
# config has to be read manually, same as this repo's old rustdesk add-on did before bashio was
# an option there either.
RUN apk add --no-cache jq

COPY run.sh /run.sh
RUN chmod a+x /run.sh

WORKDIR /app
CMD [ "/run.sh" ]
```

- [ ] **Step 4: Write `rustdesk-web/run.sh`**

```sh
#!/bin/sh
# No bashio here (see Dockerfile) -- read options.json with jq directly. set -eu is safe in
# this plain sh script (unlike the bashio-based rustdesk-server run.sh, nothing upstream of
# this script has already enabled strict mode in a way that turns benign failures fatal).
set -eu

OPTIONS_FILE="/data/options.json"
WS_HOST=""
if [ -f "${OPTIONS_FILE}" ]; then
    WS_HOST="$(jq -r '.ws_host // ""' "${OPTIONS_FILE}")"
fi

export RUSTDESK_API_LANG="en"
export RUSTDESK_API_RUSTDESK_ID_SERVER="a44b0313-rustdesk-server:21116"
export RUSTDESK_API_RUSTDESK_RELAY_SERVER="a44b0313-rustdesk-server:21117"
export RUSTDESK_API_RUSTDESK_API_SERVER="http://a44b0313-rustdesk-web:21114"
if [ -n "${WS_HOST}" ]; then
    export RUSTDESK_API_RUSTDESK_WS_HOST="${WS_HOST}"
fi

# Persist rustdesk-api's sqlite db (users, address book, logs) into Home Assistant's /data
# volume instead of the image's own /app/data, the same symlink-into-/data pattern this repo
# uses elsewhere (e.g. claude-terminal's credential directory).
if [ ! -L /app/data ]; then
    mkdir -p /data
    if [ -d /app/data ] && [ -z "$(ls -A /data 2>/dev/null)" ]; then
        cp -a /app/data/. /data/ 2>/dev/null || true
    fi
    rm -rf /app/data
    ln -s /data /app/data
fi

cd /app
exec ./apimain
```

- [ ] **Step 5: Write `rustdesk-web/CHANGELOG.md`**

```markdown
# Changelog

## 1.0.0

### Added
- Initial release: `lejianwen/rustdesk-api`'s web-admin and browser RustDesk client, served over
  Home Assistant ingress. Points at the separate `rustdesk-server` add-on over the internal
  docker network rather than bundling its own server. Replaces the old single `rustdesk`
  add-on's browser-client half, which was built on a privileged GUI-desktop image that never
  started correctly under an unprivileged ingress add-on.
- `ws_host` option for fronting the browser client's direct WebSocket connection (to
  `rustdesk-server`'s 21118/21119) with real TLS via NGINX Proxy Manager — required because
  that connection bypasses the ingress tunnel and browsers block a plain `ws://` connection
  from an HTTPS page as mixed content. See DOCS.md.
- Persistent web-admin database (users, address book, logs) under `/data`.
```

- [ ] **Step 6: Write `rustdesk-web/DOCS.md`**

```markdown
# RustDesk Web Client

Browser-accessible [RustDesk](https://rustdesk.com) client — web-admin and web client, served
from your Home Assistant sidebar over ingress. Requires the separate **RustDesk Server** add-on
(install and start that one first).

## About

This add-on runs [`lejianwen/rustdesk-api`](https://github.com/lejianwen/rustdesk-api), a
lightweight Go web-admin + browser client, pointed at the **RustDesk Server** add-on's `hbbs`/
`hbbr` over Home Assistant's internal docker network. It does not bundle its own relay server.

## Installation

1. Install and start the **RustDesk Server** add-on first.
2. Install this add-on and start it.
3. Open its sidebar panel — you land on `/_admin/`. The first-boot admin password is printed in
   this add-on's log (`ha_get_logs` / Supervisor → this add-on → Logs) — check there once after
   first start.
4. From `/_admin/`, open the web client directly, or generate a share link for a peer.

## Configuration

### Option: `ws_host`

The public `wss://` address (through your reverse proxy) the browser should use for the actual
remote-control WebSocket connection. **Required for the web client's remote-control feature to
work** — leaving it blank means `/_admin/` and login work fine, but connecting to a peer from
the browser will fail, because that connection is a WebSocket opened directly by the browser to
the **RustDesk Server** add-on's ports 21118/21119, not proxied through this add-on's ingress
tunnel. Since this add-on's own page loads over HA's HTTPS, browsers block a plain `ws://`
connection from it as mixed content — those two ports need real TLS in front of them.

**Setup with NGINX Proxy Manager** (or any reverse proxy that supports WebSocket upgrade):

1. Create two Proxy Hosts, each with "Websockets Support" enabled, both forwarding to the
   **RustDesk Server** add-on's host/IP:
   - `rustdesk-ws1.<yourdomain>` → port `21118`
   - `rustdesk-ws2.<yourdomain>` → port `21119`
2. Set `ws_host` on this add-on to `wss://rustdesk-ws1.<yourdomain>` (the exact format
   `lejianwen/rustdesk-api` expects for `RUSTDESK_API_RUSTDESK_WS_HOST` — verify against a real
   peer connection after setting this; adjust if the web client's own error message indicates a
   different expected format).
3. Restart this add-on and try connecting to a peer from the web client.

Native RustDesk clients (desktop/mobile) don't need any of this — they connect to
**RustDesk Server**'s ports directly and aren't subject to browser mixed-content rules.

| Option | Default | Description |
|---|---|---|
| `ws_host` | *(blank)* | Public `wss://` address for the browser's direct WebSocket connection. Required for remote-control to work; `/_admin/` works without it. |

## Known limitation

Whether the web client's remote-control canvas functions correctly once loaded inside HA's
ingress iframe (path-prefixed URL, pointer lock, clipboard) hasn't been verified yet — this was
flagged as an open risk during design and needs a real peer connection to confirm. If it breaks
specifically under the ingress iframe, open the web client in a new browser tab from the panel
instead of embedding it.

## Data persistence

`/data`: `lejianwen/rustdesk-api`'s sqlite database — users, address book, connection/transfer
logs, and the admin password. Losing this resets the admin account and forgets all saved peers.
```

- [ ] **Step 7: Commit**

```bash
git add rustdesk-web/
git commit -m "$(cat <<'EOF'
Add rustdesk-web add-on (browser client via lejianwen/rustdesk-api)

Replaces the browser-client half of the broken rustdesk add-on. Points at
the new rustdesk-server add-on over the internal docker network instead of
bundling its own server, and instead of the privileged GUI-desktop image
the old add-on used.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Deploy and verify `rustdesk-web` on the live instance

**Files:** none (uses HA MCP tools only)

**Interfaces:**
- Consumes: `rustdesk-web/` files from Task 3, already committed; `rustdesk-server` running
  (Task 2).

- [ ] **Step 1: Push to origin/main**

```bash
git push origin main
```

- [ ] **Step 2: Confirm the add-on is visible in the store**

Call `ha_get_addon(source="available", query="rustdesk")`. Expected: both
`a44b0313_rustdesk_server` (now `"installed":true`) and `a44b0313_rustdesk_web`
(`"installed":false`).

- [ ] **Step 3: Install**

Call `ha_manage_addon(slug="a44b0313_rustdesk_web", action="install")`.

- [ ] **Step 4: Check the build/install log**

Call `ha_get_logs(source="system_service", slug="supervisor", search="rustdesk_web", hours_back=1, order="newest", limit=100)`.
Expected: a build pulling `lejianwen/rustdesk-api:v2.7`, then
`App 'a44b0313_rustdesk_web' successfully installed`.

- [ ] **Step 5: Start it**

Call `ha_manage_addon(slug="a44b0313_rustdesk_web", action="start")`.

- [ ] **Step 6: Verify clean startup**

Call `ha_get_logs(source="supervisor", slug="a44b0313_rustdesk_web", limit=40, order="newest")`.
Expected: `apimain` starting cleanly, a printed first-boot admin password, and no repeated
connection-refused errors trying to reach `a44b0313-rustdesk-server:21116`/`:21117` (a few at
the very start while `rustdesk-server` finishes booting are fine; continuous repetition is not
— re-check Task 2's Step 8 confirmed `rustdesk-server` as `started` first).

- [ ] **Step 7: Verify the ingress panel loads**

Call `ha_get_addon(slug="a44b0313_rustdesk_web")`, note `ingress_url`. Call
`ha_manage_addon(slug="a44b0313_rustdesk_web", path="/_admin/", method="GET")` (proxy mode,
routes through ingress). Expected: an HTML response (the web-admin login page), not an error
page — confirms ingress path-prefix rewriting isn't broken for this app (the first open risk
named in the spec's Testing Plan; this step resolves the "does it even load" half of it — the
remote-control-canvas half still needs a real peer + `ws_host` configured, which is a follow-up
the user drives once they've decided on domains for NPM, per this add-on's DOCS.md).

---

### Task 5: Retire the old `rustdesk` add-on

**Files:**
- Delete: `rustdesk/` (entire directory)

**Interfaces:** none — this is cleanup only, no other task depends on it.

- [ ] **Step 1: Uninstall the old add-on from the live instance**

Call `ha_manage_addon(slug="a44b0313_rustdesk", action="uninstall")`.

- [ ] **Step 2: Confirm it's gone**

Call `ha_get_addon(slug="a44b0313_rustdesk")`. Expected: an error/not-found response (or
`"installed":false` if the slug still resolves at all post-uninstall).

- [ ] **Step 3: Delete the old add-on's files**

```bash
git rm -r rustdesk/
```

- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
Remove old rustdesk add-on (replaced by rustdesk-server + rustdesk-web)

It built hbbs/hbbr on top of linuxserver/docker-rustdesk, a privileged
GUI-desktop client image that was never a server and crash-looped without
privileges an ingress add-on shouldn't have. Split into rustdesk-server and
rustdesk-web, each on the correct upstream image for its job.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Update root README.md and push

**Files:**
- Modify: `README.md:61-76` (the existing "RustDesk Server" section)

**Interfaces:** none — documentation only.

- [ ] **Step 1: Replace the old single-add-on section with two**

Replace `README.md` lines 61-76 (the current `### RustDesk Server` section, from the heading
through its `[Documentation](rustdesk/DOCS.md)` line) with:

```markdown
### RustDesk Server

Self-hosted [RustDesk](https://rustdesk.com) relay server (`hbbs`/`hbbr`), headless — no GUI,
no browser client of its own. Pair with **RustDesk Web Client** below for browser access.

Features:
- Runs the official `rustdesk-server` `hbbs`/`hbbr` binaries, each independently auto-restarted on crash
- Persistent server identity keypair across restarts and updates
- Optional pre-shared key or encrypted-only enforcement

[Documentation](rustdesk-server/DOCS.md)

### RustDesk Web Client

Browser-accessible RustDesk client — web-admin and web client, served from your Home Assistant
sidebar over ingress. Requires the **RustDesk Server** add-on above.

Features:
- Web-admin (`/_admin/`) for managing users, address books, and share links
- Browser-based remote control of any RustDesk peer, no native app install needed
- Persistent database (users, address book, logs) across restarts and updates

Note: the browser client's remote-control connection needs a reverse proxy TLS setup beyond
ingress — see [Documentation](rustdesk-web/DOCS.md) for the NGINX Proxy Manager steps.

[Documentation](rustdesk-web/DOCS.md)
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs: update README for the rustdesk-server/rustdesk-web split

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 3: Push**

```bash
git push origin main
```
