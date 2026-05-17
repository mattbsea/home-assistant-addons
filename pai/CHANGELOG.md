# Changelog

## 1.2.0

### ✨ New Features
- **Claude Code sign-in helper**: Signing in to Claude Code from a terminal is
  awkward on mobile — the OAuth URL cannot be tapped or copied. The gateway
  now watches the terminal output for the sign-in URL and shows a banner in
  the panel with a tappable **Open sign-in page** link and a **Copy link**
  button, plus a field to paste the resulting code straight back into the
  terminal.

## 1.1.0

### ✨ New Features
- **Single tabbed sidebar panel**: The PAI sidebar panel now has **Pulse** and
  **Claude Code** tabs, so both the dashboard and the terminal are reachable
  from one place. A small gateway reverse-proxies each tab (HTTP and
  WebSocket) behind the ingress panel.

### 🔧 Changes
- The terminal is no longer exposed on a host port; it is reached only
  through the ingress panel, which Home Assistant authenticates. Removed the
  `terminal_password` option (the panel is admin-only) and the `7683` port
  mapping.

## 1.0.1

### 🐛 Bug Fixes
- **Web terminal port**: Moved the Claude Code terminal from port `7681` to
  `7683` to avoid a conflict with the Claude Terminal add-on (`7681`) and the
  Claude Terminal Dev add-on (`7682`), which prevented the add-on from
  starting when those were installed.

## 1.0.0

### ✨ New Add-on
- **Personal AI Infrastructure (PAI)**: Runs [Daniel Miessler's PAI](https://github.com/danielmiessler/Personal_AI_Infrastructure)
  in Home Assistant.
  - **Pulse dashboard**: clones the PAI repository, installs it, and serves
    the *PAI Observatory* through Home Assistant ingress as a sidebar panel.
    The Next.js dashboard is automatically rebuilt with the ingress base path
    so assets, routes and API calls resolve correctly behind the proxy.
  - **Claude Code terminal**: a password-protected web terminal with the
    Claude Code CLI preinstalled, exposed on port `7683`, for PAI setup such
    as the `/interview` wizard.
  - Generates a clean, headless-friendly `PULSE.toml` (Telegram/iMessage
    bridges and personal cron jobs disabled to avoid log noise).
  - Options: `pai_ref`, `update_on_start`, `enable_terminal`,
    `terminal_password`, `elevenlabs_api_key`, `extra_env`.
  - Builds for `amd64` and `aarch64`.
