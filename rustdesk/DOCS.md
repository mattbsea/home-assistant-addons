# RustDesk Server

Run a self-hosted [RustDesk](https://rustdesk.com) server — `hbbs` (ID/rendezvous) and `hbbr`
(relay) — inside Home Assistant, with a status and connection-info dashboard in the sidebar.

## About

RustDesk is an open-source remote-desktop tool. Self-hosting your own `hbbs`/`hbbr` server means
your connection metadata (and, with `always relay`, all traffic) goes through your own
infrastructure instead of RustDesk's public servers.

**Important — read before installing:** this add-on's ingress web panel is a **status/connection
dashboard**, not an in-browser remote-desktop viewer. Home Assistant's ingress proxies a single
HTTP(S)+WebSocket port; RustDesk's actual remote-desktop protocol needs raw TCP and UDP on ports
21115–21119, which ingress cannot carry. You still need:

- The [native RustDesk client](https://rustdesk.com/download) on any machine you connect *from*,
  and a RustDesk-compatible agent running on any machine you connect *to*.
- Router port-forwards for 21115–21119 (21116 also needs UDP) to this add-on if you want to
  connect from outside your LAN. The official [web client](https://rustdesk.com/web) works too,
  once those ports are reachable — point it at your server's public address and the key shown in
  the dashboard.

## Installation

1. Add this repository to your Home Assistant Add-on Store.
2. Install **RustDesk Server**.
3. Set `relay_host` (see Configuration) if you want remote access, not just LAN.
4. Start the add-on and open its sidebar panel for status and connection details.

## Configuration

### Option: `relay_host`

The public IP address or DDNS hostname clients should use to reach this server. Leave blank for
LAN-only use (the dashboard falls back to the add-on's local IP, which isn't reachable from
outside your network). Forward ports 21115–21119 (21116 also UDP) to this add-on on your router
when set.

### Option: `encrypted_only`

When `true` (default), connections must be encrypted — `hbbs`/`hbbr` are started with `-k _`,
which requires clients to use the server's auto-generated key. Set to `false` only if you have a
specific reason to allow unencrypted connections.

### Option: `custom_key`

An optional fixed pre-shared key (`-k <value>`), used instead of the auto-generated encryption key
when set. Most installs should leave this blank and use the auto-generated key shown in the
dashboard.

| Option | Default | Description |
|---|---|---|
| `relay_host` | *(blank)* | Public address clients use to reach this server. Blank = LAN only. |
| `encrypted_only` | `true` | Require encrypted connections. |
| `custom_key` | *(blank)* | Optional fixed pre-shared key instead of the auto-generated one. |

## Usage

Open the add-on's sidebar panel. It shows:

- Live status of `hbbs` and `hbbr`
- The server's public key (paste into each client's Settings → Network → Key)
- ID server / relay server addresses to paste into clients
- The ports you need to forward for remote access
- Recent `hbbs`/`hbbr` log output

Configure each RustDesk client's Settings → Network with the ID Server and Relay Server addresses
and Key shown on the dashboard.

## Features

- Runs the official `rustdesk-server` `hbbs`/`hbbr` binaries
- Auto-generates and persists the server's identity keypair across restarts (`/data/rustdesk`)
- Status/connection dashboard served over Home Assistant ingress
- Optional pre-shared key or encrypted-only enforcement

## Data persistence

The server's identity keypair (`id_ed25519` / `id_ed25519.pub`) is stored under `/data/rustdesk`
and survives add-on restarts and updates. **Do not delete `/data`** — every client that has
already imported the old public key will refuse to connect (or show a "key mismatch" warning)
after the keypair changes.
