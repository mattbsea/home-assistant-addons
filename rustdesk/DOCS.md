# RustDesk Server

Self-hosted [RustDesk](https://rustdesk.com) in one add-on: the server (`hbbs`/`hbbr`) and a
**browser-accessible RustDesk client**, both reachable from your Home Assistant sidebar over
ingress — no native app install required to *use* RustDesk from your phone or a borrowed browser.

## About

This add-on runs [`linuxserver/docker-rustdesk`](https://github.com/linuxserver/docker-rustdesk)
— the official native RustDesk desktop client, sandboxed and streamed to your browser via
[Selkies](https://github.com/selkies-project/selkies) — and layers the official `rustdesk-server`
binaries (`hbbs`, `hbbr`) on top, so you get a self-hosted server *and* a working client in one
place.

**What "web client" means here:** opening the add-on's sidebar panel drops you into the actual
RustDesk desktop app, streamed live — you can add peers, enter IDs/keys, and control remote
machines exactly as you would with the native app, just running in your browser. It is genuinely
functional, not a mockup — but it's still a *client*: to control *other* machines you still need
the native RustDesk client (or an unattended-access agent) running on those machines.

## Installation

1. Add this repository to your Home Assistant Add-on Store.
2. Install **RustDesk Server**.
3. Set `relay_host` (see Configuration) if you want devices outside your LAN to reach your
   self-hosted server.
4. Start the add-on and open its sidebar panel — the streamed RustDesk desktop opens directly.

## Configuration

### Option: `relay_host`

The public IP address or DDNS hostname clients should use to reach `hbbs`/`hbbr`. Leave blank for
LAN-only use. Forward ports 21115–21119 (21116 also UDP) to this add-on on your router when set.

### Option: `encrypted_only`

When `true` (default), connections to `hbbs`/`hbbr` must be encrypted (`-k _`, requiring the
server's auto-generated key). Set to `false` only if you have a specific reason to allow
unencrypted connections.

### Option: `custom_key`

An optional fixed pre-shared key (`-k <value>`) for `hbbs`/`hbbr`, used instead of the
auto-generated key when set.

| Option | Default | Description |
|---|---|---|
| `relay_host` | *(blank)* | Public address clients use to reach `hbbs`/`hbbr`. Blank = LAN only. |
| `encrypted_only` | `true` | Require encrypted connections to the server. |
| `custom_key` | *(blank)* | Optional fixed pre-shared key instead of the auto-generated one. |

## Usage

Open the add-on's sidebar panel to use the streamed RustDesk client directly. To find your
self-hosted server's own ID/key (e.g. to configure an unattended-access agent to register with
it), open the client's own Settings → Network screen inside the streamed desktop — the server
generates and displays its identity there like any RustDesk deployment.

## Features

- Runs the official RustDesk client, sandboxed and streamed to your browser (no native install)
- Runs the official `rustdesk-server` `hbbs`/`hbbr` binaries alongside it
- Both processes supervised and auto-restarted by the image's own init if they crash
- Persistent server identity keypair and client settings across restarts (`/data`)
- Optional pre-shared key or encrypted-only enforcement for the server

## Security notes

- The streamed desktop's own HTTP Basic Auth is intentionally **left disabled** — access is
  gated by Home Assistant ingress (logged-in HA users only) instead. **Do not** port-forward or
  otherwise expose the add-on's internal port 3000 directly; it has no auth of its own and isn't
  declared in this add-on's `ports:` list for exactly that reason.
- Ports 21115–21119 are the actual RustDesk wire protocol (separately keyed/encrypted per
  `encrypted_only`/`custom_key` above) — forwarding those is expected and required for remote
  server access, unlike port 3000.

## Known limitations

- **No armv7 support.** `linuxserver/docker-rustdesk` publishes amd64/arm64 only.
- **Shared-memory size.** Selkies' own docs recommend `--shm-size=1gb` for streaming stability;
  Home Assistant's add-on `config.yaml` has no option to configure this, so the container runs
  with Docker's 64MB default. If you see stuttering or crashes under sustained use, this is the
  most likely cause — there is currently no add-on-side workaround.
- **CPU**: smooth streaming benefits from an AVX2-capable CPU (Haswell-class x86_64, 2013+) for
  Selkies' zero-copy encoding path; older/ARM hardware falls back to a slower software path.
- Home Assistant's ingress WebSocket proxying has occasional reported flakiness under sustained,
  high-throughput WebSocket traffic (seen with other add-ons, e.g. zwave-js-ui) — Selkies streams
  continuous video/audio/input over one persistent WebSocket, which is exactly that kind of load.
  If the stream stutters or disconnects, this is a known open question with HA's ingress itself,
  not something specific to this add-on.

## Data persistence

- `/data/rustdesk`: the server's identity keypair (`id_ed25519`/`id_ed25519.pub`). **Do not
  delete `/data`** — every client that already trusts the old public key will refuse to connect
  (or show a "key mismatch" warning) after the keypair changes.
- `/data/config`: the streamed client's own settings, saved peers, and passwords.
