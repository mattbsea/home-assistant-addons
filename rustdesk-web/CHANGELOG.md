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

### Fixed
- **Opening the add-on from the sidebar 404'd.** `rustdesk-api`'s root handler issues a
  hardcoded absolute redirect (`/_admin/`) with no awareness of HA ingress's per-session path
  prefix, sending the browser to `<ha-host>/_admin/` — a route HA itself doesn't have. Fronted
  apimain with a small nginx that rewrites the `Location` header using `X-Ingress-Path`, the
  prefix HA's ingress proxy sends for exactly this purpose. (`nginx.conf`, `run.sh`)
- **The admin panel loaded but hung on an infinite spinner.** The compiled admin SPA also
  hardcodes its API calls to the domain-absolute path `/api/admin`, which has the same
  reverse-proxy-prefix blindness as the redirect above — except this one is the browser's own JS
  constructing the URL client-side, so no server-side rewrite could fix it. Patched the compiled
  bundle at build time to compute the real prefix from the current page's own path instead.
  (`Dockerfile`)
- **Accessing this add-on through a plain reverse proxy (e.g. NGINX Proxy Manager, for a native
  client's "API server" field) redirected to this container's own internal address and port
  instead of the public domain** — `http://<public-domain>:21114/_admin/`, which doesn't work
  (wrong port, downgraded to plain HTTP). Root cause: nginx auto-qualifies a bare-path
  `proxy_redirect` replacement using its own internal scheme/host/port whenever it doesn't
  already have one, and that internal view is never what the actual browser is talking to
  through a reverse proxy that (unlike Home Assistant's own ingress proxy) doesn't rewrite
  Location headers on the way back out. Now builds the correct absolute URL explicitly from
  `X-Forwarded-Proto`/`X-Forwarded-Host` (which your reverse proxy needs to send — see "Reaching
  this add-on directly" in DOCS.md) before nginx gets a chance to guess wrong. (`nginx.conf`)
