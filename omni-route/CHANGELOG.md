# Changelog

## 1.0.9

- **Fix doubled Supervisor slug (this is why the sidebar link 404'd, e.g.
  `/a44b0313_a44b0313_omni_route`):** `config.yaml`'s `slug` was hardcoded to
  `a44b0313_omni_route`. Supervisor always prepends the repository hash
  (`a44b0313`) to whatever `slug` is set, so the installed add-on ended up
  registered under a doubled slug that no sidebar/panel route resolves to.
  Every other add-on in this repo uses a bare slug (`fleet_telemetry`,
  `teslausb_viewer`, ...); `slug` is now `omni_route` to match. **Existing
  installs must be uninstalled and reinstalled** — a slug change doesn't
  apply retroactively to an already-installed add-on.
- **Fix sidebar Ingress 404/blank-dashboard for real:** OmniRoute (Next.js) has
  no Ingress awareness. Supervisor strips the `/api/hassio_ingress/<token>`
  prefix before forwarding to the add-on and never rewrites response bodies or
  headers on the way back, so OmniRoute's root-absolute redirects
  (`/` -> `/dashboard`) *and* its `/_next/static/*` asset URLs escaped the
  Ingress-proxied path — the actual cause behind every prior "fix ingress 404"
  release (pointing `webui` at `/dashboard` only fixed the separate "Open Web
  UI" button, not the sidebar panel). OmniRoute now runs on an internal-only
  port (20130) with `OMNIROUTE_BASE_PATH` set to this add-on's stable Ingress
  entry path (via `bashio::app.ingress_entry`), so Next.js's own basePath
  support prefixes every redirect, asset, and link it emits. A new
  `ingress-proxy.js` serves the real Ingress port (20128) and re-adds that
  same prefix to incoming requests, so both Ingress and direct/LAN access
  land in the same "prefixed" address space OmniRoute now expects.
- Add HTTP request logging to `ingress-proxy.js` (method, path, rewritten
  target, status, timing) at `log_level: debug`, to make future Ingress
  connectivity issues visible without guessing.
- Fix dead `SERVER_PORT` env var: OmniRoute reads `PORT`, not `SERVER_PORT`.
  Was silently masked because OmniRoute's own shipped default also happens to
  be 20128.

## 1.0.8

- Fix ingress 404: point webui to /dashboard (bypasses OmniRoute's / → /dashboard redirect)

## 1.0.7

- Remove omniroute-reset-password dependency, use INITIAL_PASSWORD env var
- Add debug logging for PATCH status codes

## 1.0.6

- Fix BusyBox grep compatibility (use sed instead of grep -oP)

## 1.0.5

- Fix ingress 404: authenticate before disabling requireLogin when password is set

## 1.0.4

- Enable HTTP request logging (console + file)
- Add log_level option (debug/info/warn/error)
- Request logs retained 30 days, up to 200k rows

## 1.0.3

- Fix ingress 404: disable dashboard login requirement after startup
- Add curl for in-container API calls

## 1.0.2

- Fix base image (use hassio-addons/base:21.0.0 instead of archived base-nodejs)
- Install Node.js and npm via apk

## 1.0.1

- Fix Dockerfile base image

## 1.0.0

- Initial release
- OmniRoute AI gateway with 268+ providers
- Ingress dashboard
- Auto-fallback routing
- Token compression (RTK + Caveman)
