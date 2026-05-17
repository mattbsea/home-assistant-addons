# Changelog

## 1.0.0

### ✨ New Add-on
- **Personal AI Infrastructure (PAI)**: Runs the Pulse Life Dashboard daemon
  from [danielmiessler/Personal_AI_Infrastructure](https://github.com/danielmiessler/Personal_AI_Infrastructure)
  - Clones the PAI repository on first start and keeps it in `/data`
  - Installs the PAI payload into `~/.claude` and serves the *PAI Observatory*
    dashboard through Home Assistant ingress on port `31337`
  - Generates a clean, headless-friendly `PULSE.toml` (Telegram/iMessage
    bridges and personal cron jobs disabled to avoid log noise)
  - Optional ElevenLabs API key and `extra_env` configuration
  - Builds for `amd64` and `aarch64`
