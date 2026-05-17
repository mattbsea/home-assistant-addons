# Changelog

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
