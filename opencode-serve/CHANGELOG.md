# Changelog

## 1.2.4

- Fix: OpenCode's generated API client issues requests as `fetch(new Request(url, init))`. The
  ingress fetch patch only rewrote plain string URLs, so every API call silently passed through
  unmodified and hit Home Assistant Core's own `/api/` endpoint at the bare origin instead of the
  add-on. The patch now rewrites the URL of `Request` objects too.
- Consolidated the duplicated ingress-patch.js logic (previously generated inline in `run.sh` via
  heredoc, diverged from the checked-in `ingress-patch.js` file which was never actually built into
  the image) into a single template, substituted at container start the same way as
  `nginx.conf.template`.

## 1.2.3

- Fix: patch absolute URLs (origin + `/api/`) in addition to relative paths.

## 1.2.2

- Fix: include `ingress-patch.js` in repo (was untracked).

## 1.2.1

- Add runtime ingress patching for SPA API calls (fetch/EventSource monkey-patch).
- Add nginx debug logging (access/error log to stdout/stderr).

## 1.2.0

- Reset to a verbatim copy of the upstream `drakonizer/opencode-ha-addon` reference add-on, with
  ingress support layered back on top.

## 1.1.1

- Fix: nginx MIME type fix.

## 1.1.0

- Add nginx middleware for ingress path rewriting (`sub_filter` on HTML/asset references).
- Fix nginx duplicate MIME type warning.

## 1.0.1

- Fix: Remove invalid `--dir` flag from opencode serve command

## 1.0.0

- Initial release
- Runs OpenCode web interface via `opencode serve`
- Ingress panel with sidebar link
- Persistent config and workspace storage in `/data`
- Pinned to opencode-ai@1.18.5
