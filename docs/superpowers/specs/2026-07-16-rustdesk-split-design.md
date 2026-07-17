# RustDesk: split into server + web-client add-ons — Design

## Purpose

The current `rustdesk` add-on (slug `a44b0313_rustdesk`) is broken: it builds
hbbs/hbbr (the actual RustDesk relay server) on top of
`lscr.io/linuxserver/rustdesk`, which is **not** a server image. Per its own
README: *"This is the Desktop application in a web accessible format!
... It is not a server solution!"* It's a full privileged GUI desktop
(KDE/Wayland via Selkies) with the RustDesk **client** app installed inside,
requiring `/dev/dri`, `/dev/uinput`, and generally privileged access, and
shipping a passwordless-sudo terminal in the browser. Running it unprivileged
(correctly, for an add-on sitting behind ingress) crash-loops nginx (missing
self-signed cert) and panics the input/GPU threads (`PermissionDenied` on
`/dev/uinput`).

Replace it with two purpose-built add-ons, each on the correct upstream image
for its actual job.

## Scope

- amd64 + aarch64 (matches the current add-on; both upstream images publish
  both architectures).
- Same functional goal as the original add-on: a self-hosted RustDesk relay
  server reachable by native clients over the internet, plus a
  browser-accessible client served over HA ingress.
- Not in scope: auto-provisioning the NPM TLS setup the web client needs
  (documented as a manual DOCS.md step, matching most add-ons in this repo —
  fleet-telemetry's NPM auto-wizard is the exception, built for a stricter
  mTLS requirement that doesn't apply here).

## Architecture

```
Native RustDesk clients (desktop/mobile) ──TCP/UDP :21115-21117──▶ rustdesk-server
                                                                     (hbbs + hbbr)
Browser (HA ingress, logged-in HA user) ──HTTPS──▶ rustdesk-web :21114 (API + web-admin + web client)
                                                       │
                                                       │ internal docker network
                                                       ▼
                                                   rustdesk-server :21116 (ID) / :21117 (relay)

Browser's own WebSocket (remote-control data path, bypasses ingress) ──WSS──▶ NPM ──▶ rustdesk-server :21118/:21119
```

- **`rustdesk-server`**: official `rustdesk/rustdesk-server` hbbs/hbbr
  binaries (already vendored correctly in the current add-on's Dockerfile —
  see Components below), rehosted on a plain HA base image instead of
  `linuxserver/docker-rustdesk`. No GUI, no privileged access, no ingress —
  it's headless. Ports 21115–21117 (TCP/UDP) stay published to the host and
  port-forwarded from the router, same as today, for native clients.
  21118/21119 (hbbs/hbbr's own WebSocket listeners — confirmed present in
  `rustdesk-server`'s source, no fork needed) also stay published, since the
  *browser's* WebSocket connection reaches them directly rather than through
  ingress.

- **`rustdesk-web`**: `lejianwen/rustdesk-api` (the plain image, **not**
  `:full-s6` — that variant bundles its own hbbs/hbbr, which would mean two
  independent, un-synced RustDesk servers running side by side). Serves
  `/_admin/` (web-admin) and the browser RustDesk client on port 21114,
  exposed over ingress. Configured via `RUSTDESK_API_RUSTDESK_ID_SERVER` /
  `_RELAY_SERVER` / `_API_SERVER` to point at the `rustdesk-server` add-on's
  internal hostname (`a44b0313-rustdesk-server`) for API-to-server plumbing.

- **The TLS wrinkle**: the web client's actual remote-control data path is a
  WebSocket opened directly by the browser's JS to `rustdesk-server`'s
  21118/21119 — it does **not** run through the ingress tunnel, because
  browsers can't originate arbitrary WebSocket connections through an
  iframe's ingress proxy to non-ingress-port endpoints. Since the page
  itself loads over HA's HTTPS, browsers block a plain `ws://` connection
  from it as mixed content, so 21118/21119 need real TLS in front of them.
  Unlike fleet-telemetry's mTLS Stream (which needs raw TCP passthrough
  because the add-on itself validates the client cert), this is a plain
  WebSocket with no client-cert requirement — a standard **NPM Proxy Host per
  port with "Websockets Support" enabled** (two subdomains, e.g.
  `rustdesk-ws1./ws2.mbarclay.org` → add-on `:21118`/`:21119`) is sufficient;
  no raw Stream passthrough needed. `RUSTDESK_API_RUSTDESK_WS_HOST` on the
  `rustdesk-web` add-on points at the public `wss://` address(es) through
  NPM, not the internal docker hostname.

  `rustdesk-server` still keeps 21118/21119 host-published (router
  port-forwarded) independently of NPM — that path serves plain `ws://` for
  LAN clients where mixed-content blocking doesn't apply (nothing loads
  those pages over HTTPS), while NPM's `wss://` path is what makes the
  browser client work when reached through HA's own HTTPS frontend. The two
  aren't redundant: same backend port, two different front doors for two
  different caller contexts.

## Configuration

**`rustdesk-server/config.yaml`** — carries over unchanged from the current
add-on:
```yaml
options:
  relay_host: ""
  encrypted_only: true
  custom_key: ""
schema:
  relay_host: "str?"
  encrypted_only: "bool"
  custom_key: "password?"
ports:
  21115/tcp: 21115
  21116/tcp: 21116
  21116/udp: 21116
  21117/tcp: 21117
  21118/tcp: 21118
  21119/tcp: 21119
map:
  - data:rw   # hbbs/hbbr identity keypair
```
No `ingress`, no `build.yaml` GUI base image — the Dockerfile builds `FROM`
a plain HA base image (matching claude-terminal/fleet-telemetry's pattern).

**`rustdesk-web/config.yaml`** — new:
```yaml
ingress: true
ingress_port: 21114
panel_icon: mdi:monitor-share
panel_title: "RustDesk"
options:
  ws_host: ""       # public wss:// host(s) for the browser's direct WebSocket connection
schema:
  ws_host: "str?"
map:
  - data:rw   # rustdesk-api's sqlite db (users, address book, logs)
```

## Components

- **`rustdesk-server/Dockerfile`** — carries over the current add-on's
  hbbs/hbbr-fetch logic almost verbatim (pinned release .deb extraction via
  `dpkg-deb -x`, already correct), just `FROM` a plain HA base image instead
  of `linuxserver/docker-rustdesk`, and drops everything specific to that
  base (the `/config` symlink dance in `custom-cont-init.d`, the Selkies/
  s6-overlay-extension-point wiring). `custom-services.d/hbbs` and `hbbr`
  carry over unchanged (they already read `/data/options.json` directly and
  exec the right binary with the right args).
- **`rustdesk-web/Dockerfile`** — thin wrapper: `FROM lejianwen/rustdesk-api`
  (pinned tag), a small init script that reads `/data/options.json` for
  `ws_host` and writes the `RUSTDESK_API_RUSTDESK_WS_HOST` /
  `_ID_SERVER` / `_RELAY_SERVER` / `_API_SERVER` env vars (ID/relay/API
  server values are fixed — they always point at the `rustdesk-server`
  add-on's internal hostname — only `ws_host` is user-configured, since it
  depends on the user's own domain/NPM setup).
- **`rustdesk-web/DOCS.md`** — manual NPM setup steps (two Proxy Hosts with
  websockets support, matching the pattern already documented in this repo
  for other add-ons that need external TLS termination), plus the note that
  native RustDesk clients don't need any of this — they talk to
  `rustdesk-server` directly.

## Error Handling

- `rustdesk-web` can't reach `rustdesk-server`'s internal hostname (e.g.
  server add-on not started) → `lejianwen/rustdesk-api` already logs its own
  connection errors on startup; no special handling needed beyond making the
  dependency clear in DOCS.md (start `rustdesk-server` first).
- `ws_host` left blank → web client loads and the API/admin UI works, but the
  browser's remote-control WebSocket fails (mixed-content block); DOCS.md
  flags this as the expected state until NPM is configured, not a bug.
- `rustdesk-server` restart → its `/data` keypair persists (unchanged
  volume-mapping pattern from the current add-on), so its identity and
  existing peer trust survive.

## Testing Plan

- Local build of both add-ons via `podman build`, matching the pattern in
  CLAUDE.md.
- Bring up `rustdesk-server` alone first; confirm hbbs/hbbr start cleanly
  (no crash loop) and a native RustDesk desktop client can register an ID
  against it on the LAN.
- Bring up `rustdesk-web`; confirm `/_admin/` loads over ingress and shows
  the server as reachable.
- **Open risk to resolve during implementation** (carried over from the
  earlier throwaway spike at `.planning/spikes/rustdesk-webclient/`, never
  completed): does the web client's remote-control canvas actually work
  once framed under HA ingress's path-prefixed URL (asset paths, pointer
  lock, clipboard)? Test with a real peer once `ws_host`/NPM is configured.
  Fallback if it breaks specifically under the ingress iframe: open the web
  client in a new browser tab from the panel instead of embedding it,
  per the spike's own decision matrix.

## Migration

- Uninstall `a44b0313_rustdesk` (stop + remove via Supervisor).
- Delete the `rustdesk/` directory.
- Add `rustdesk-server/` and `rustdesk-web/` directories.
- No data to carry over — the current add-on has never successfully started,
  so its `/data/rustdesk` identity keypair was never used by any real peer.

## Out of Scope (v1)

- NPM auto-configuration wizard (fleet-telemetry-style) for the WebSocket
  TLS setup — manual DOCS.md steps are sufficient for a single-user setup.
- `lejianwen/rustdesk-api`'s OAuth/LDAP/multi-user features — defaults
  (single local admin account, generated on first boot) are sufficient.
- Fixing the ingress-iframe remote-control risk proactively — validate
  during implementation instead; the new-tab fallback is cheap if needed.
