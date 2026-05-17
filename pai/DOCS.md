# Personal AI Infrastructure (PAI)

Runs [Daniel Miessler's PAI](https://github.com/danielmiessler/Personal_AI_Infrastructure)
inside Home Assistant: the **Pulse** dashboard and a **Claude Code** web
terminal.

## About

PAI is a Claude Code-based "Life Operating System". This add-on provides two
ways to use it from your browser:

- **PAI Pulse dashboard** — the *PAI Observatory*, served through Home
  Assistant ingress and shown as a sidebar panel.
- **Claude Code terminal** — a web terminal with the Claude Code CLI
  preinstalled, for running PAI setup such as the `/interview` wizard.

On first start the add-on clones the upstream PAI repository, installs the
`.claude` payload, rebuilds the dashboard so it works behind ingress, and
starts both services.

## Installation

1. Add this repository to your Home Assistant add-on store (if not already).
2. Install the **Personal AI Infrastructure** add-on.
3. Start it. The first start downloads PAI and **rebuilds the dashboard**,
   which can take several minutes — watch the add-on log for progress.
4. Open the **PAI Pulse** panel from the sidebar for the dashboard, or use
   **Open Web UI** for the Claude Code terminal.

## Accessing PAI

| What            | How                                    | Notes                         |
|-----------------|----------------------------------------|-------------------------------|
| Pulse dashboard | **PAI Pulse** sidebar panel (ingress)  | Admin users only              |
| Claude terminal | **Open Web UI** button (port `7683`)   | Password protected            |

### The dashboard and ingress

The *PAI Observatory* is a Next.js application that uses absolute asset and
API paths. To work behind the Home Assistant ingress proxy it is rebuilt on
start with the add-on's ingress path baked in as its base path. This is
automatic; it only re-runs when PAI updates or the ingress path changes.

### The Claude Code terminal

The terminal runs the Claude Code CLI as `pai`. Use it to authenticate Claude
Code and run PAI commands such as `/interview`. Because it exposes a shell, it
is protected with HTTP basic authentication — see `terminal_password` below.

## Configuration

```yaml
pai_ref: main
update_on_start: true
enable_terminal: true
terminal_password: ""
elevenlabs_api_key: ""
extra_env: []
```

### Option: `pai_ref`

Git branch or tag of the PAI repository to use. Defaults to `main`.

### Option: `update_on_start`

When `true` (default), the add-on fetches the latest PAI code on each start
and rebuilds the dashboard. Set to `false` to pin the downloaded version.

### Option: `enable_terminal`

When `true` (default), the Claude Code web terminal runs on port `7683`. Set
to `false` to run only the dashboard.

### Option: `terminal_password`

Password for the web terminal (username is always `pai`). If left empty, a
random password is generated on each start and printed to the add-on log —
set a value here for a stable login.

### Option: `elevenlabs_api_key`

Optional [ElevenLabs](https://elevenlabs.io) API key. When set, the Pulse
voice module is enabled and the key is written to `~/.claude/.env`.

### Option: `extra_env`

Optional list of `KEY=VALUE` strings appended to `~/.claude/.env`, for PAI
features you enable yourself:

```yaml
extra_env:
  - "TELEGRAM_BOT_TOKEN=123456:abcdef"
```

## How it works

- Data lives under `/data` (the add-on's persistent volume):
  - `/data/pai-src` — the cloned PAI git repository
  - `/data/home/.claude` — the installed PAI payload (and Claude Code config)
- The add-on writes a minimal `PULSE.toml` on every start. The Telegram and
  iMessage bridges and the author's personal cron jobs are disabled, because
  in a headless container they only produce log noise.

## Ports

| Port    | Description                                                  |
|---------|--------------------------------------------------------------|
| `7683`  | Claude Code web terminal (basic-auth protected).             |

The dashboard is served through ingress and does not use a host port.

## Limitations

- Supported architectures are `amd64` and `aarch64` only — the Bun runtime
  and the ttyd binary have no `armv7` build.
- The web terminal exposes a shell inside the add-on container; keep
  `terminal_password` set to a strong value.
- The add-on needs outbound internet access to clone PAI, install the Bun
  runtime, and build the dashboard.
