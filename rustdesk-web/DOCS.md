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
