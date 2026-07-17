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
