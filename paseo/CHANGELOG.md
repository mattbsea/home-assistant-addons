# Changelog

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
