# Personal AI Infrastructure (PAI)

Runs **Pulse**, the Life Dashboard daemon from
[danielmiessler/Personal_AI_Infrastructure](https://github.com/danielmiessler/Personal_AI_Infrastructure).

## About

PAI is a Claude Code-based "Life Operating System". Pulse is its unified
daemon: a single process that serves the *PAI Observatory* web dashboard and
hosts the observability, performance and hook subsystems.

This add-on runs Pulse headlessly inside Home Assistant. On first start it
clones the upstream PAI repository, installs the `.claude` payload into a
persistent location, writes a clean add-on-managed `PULSE.toml`, and starts
the daemon. The dashboard is served on port `31337` and through Home
Assistant ingress.

## Installation

1. Add this repository to your Home Assistant add-on store (if not already).
2. Install the **Personal AI Infrastructure** add-on.
3. Start the add-on. The first start downloads the PAI repository and may
   take a minute or two.
4. Open the **PAI Pulse** panel from the sidebar (or "Open Web UI").

## Configuration

```yaml
pai_ref: main
update_on_start: true
elevenlabs_api_key: ""
extra_env: []
```

### Option: `pai_ref`

Git branch or tag of the PAI repository to use. Defaults to `main`.

### Option: `update_on_start`

When `true` (default), the add-on fetches the latest PAI code each time it
starts. Set to `false` to pin the currently downloaded version.

### Option: `elevenlabs_api_key`

Optional. An [ElevenLabs](https://elevenlabs.io) API key. When set, the Pulse
voice module is enabled and the key is written to `~/.claude/.env`. Leave
empty to run without voice.

### Option: `extra_env`

Optional list of `KEY=VALUE` strings appended to `~/.claude/.env`. Use this
to supply tokens for PAI features you enable yourself, for example:

```yaml
extra_env:
  - "TELEGRAM_BOT_TOKEN=123456:abcdef"
```

## How it works

- Data lives under `/data` (the add-on's persistent volume):
  - `/data/pai-src` — the cloned PAI git repository
  - `/data/home/.claude` — the installed PAI payload, including `PULSE.toml`
- The add-on writes a minimal `PULSE.toml` on every start. The Telegram and
  iMessage bridges and the author's personal cron jobs are disabled, because
  in a headless container they only produce log noise. Edit the file under
  `/data/home/.claude/PAI/PULSE/PULSE.toml` only if you also set
  `update_on_start: false` — otherwise changes are overwritten.

## Ports

| Port      | Description                                              |
|-----------|----------------------------------------------------------|
| `31337`   | Pulse Life Dashboard. Not required when using ingress.   |

## Accessing the dashboard

The dashboard is reachable two ways:

- **Ingress** — the **PAI Pulse** sidebar panel.
- **Direct port** — the **Open Web UI** button (port `31337`).

The *PAI Observatory* is a prebuilt Next.js app that references its assets by
absolute path. If the ingress panel ever renders without styling, use **Open
Web UI** instead, which always serves the dashboard correctly.

## Limitations

- Supported architectures are `amd64` and `aarch64` only — the Bun runtime
  has no `armv7` build.
- This add-on runs the **Pulse daemon and dashboard**. The full PAI
  experience (the `/interview` wizard, agentic skills, Digital Assistant
  loop) is driven by Claude Code and is out of scope for a headless add-on.
- Pulse needs outbound internet access to clone/update the PAI repository.
