# Changelog

## 1.4.0

### ✨ New Features
- **Paste into the terminal**: A **Paste** button now appears on the Claude
  Code tab. It opens a box you can paste or type text into and then send to
  the terminal — with or without a trailing Enter. Pasting directly into a
  web terminal is unreliable on mobile; this provides a dependable way to get
  text in.

## 1.3.0

### ✨ New Features
- **Persistent terminal session**: The Claude Code terminal now runs inside a
  `tmux` session. Previously, switching away from the Home Assistant app —
  for example to complete the Claude sign-in in a browser — dropped the
  connection and restarted Claude Code, invalidating the in-progress login.
  The session now stays alive in the background; reconnecting re-attaches to
  it, so you can leave to sign in and come back to the same prompt. Mouse
  scrolling is also enabled for easier use on touch devices.

## 1.2.1

### 🐛 Bug Fixes
- **User data is preserved across updates**: Previously every start re-copied
  the upstream PAI files over the install, which could revert files edited
  through the terminal or the `/interview` wizard. Updates now refresh only
  the framework and never overwrite user-modifiable data — the `MEMORY` and
  `PAI/USER` zones, `settings.json` (Digital Assistant identity) and
  `.mcp.json`. The first install still seeds the full payload, and anything
  in the home directory outside `~/.claude` was already untouched.

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
