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
