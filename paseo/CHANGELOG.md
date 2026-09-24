# Changelog

## 1.1.1

### Changed
- Removed the setup page's "Skip" link: once skipped there was no way back to the password
  prompt. A browser with no saved hosts now always gets the prompt, and the page can be
  reopened any time at `/paseo-setup.html`.

## 1.1.0

### Added
- The web UI now configures itself for this add-on's daemon out of the box. Paseo's web app
  only auto-connects to the daemon that served it when no password is set, so a fresh install
  showed an empty "add host" screen. The image now ships `/paseo-setup.html`, which saves the
  same-origin daemon (endpoint, TLS, password) into the app's saved hosts and loads the app:
  - the sidebar panel opens it with the password (in the URL fragment), so it connects with no
    typing at all;
  - any browser with no saved hosts that opens the web UI is redirected to it and only has to
    enter the password (or pick "Skip").

## 1.0.1

### Fixed
- Paseo terminals and agent shells ran under `/bin/sh` (dash) because `SHELL` was unset;
  it is now `/bin/bash`.

## 1.0.0

### Added
- Initial release, built on the official `ghcr.io/getpaseo/paseo:0.9.2` image (daemon + bundled
  web UI) with the `claude`, `codex` and `opencode` CLIs installed.
- Password protection on by default: an empty `password` option generates a random password on
  first start and writes it back to the add-on's own options via the Supervisor API.
- Sidebar panel (ingress) that frames `external_url`, since Paseo's web app cannot run under
  the ingress path prefix.
- Persistent `/data/home` for Paseo state and agent logins; `/share/paseo` workspace.
- Pinned agent CLIs (claude-code 2.1.281, codex 0.156.1, opencode 1.18.32); `SUPERVISOR_TOKEN` is
  removed from the environment agents inherit.
- Optional `claude_code_oauth_token`, `anthropic_api_key` and `openai_api_key` options.
- No bashio: the upstream image is Debian-based without it, so `run.sh` uses `jq` and `curl`
  directly (same approach as `rustdesk-web`).
