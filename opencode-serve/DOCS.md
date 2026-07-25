# OpenCode - Home Assistant Add-on

AI coding agent web interface powered by [OpenCode](https://opencode.ai).

## Installation

1. Add this repository to Home Assistant Supervisor
2. Navigate to **Settings → Add-ons → Add-on Store**
3. Find **OpenCode** and click **Install**

## Access

Click the **OpenCode** link in the sidebar to open the web interface.

## Configuration

| Option | Default | Description |
|---|---|---|
| `log_level` | `info` | Verbosity of add-on logs (`debug`, `info`, `warn`, `error`) |

## API Keys

Configure your AI provider API keys (Anthropic, OpenAI, etc.) through the OpenCode web interface after installation. Configuration is persisted in `/data`.

## How It Works

The add-on runs `opencode serve`, which starts a headless web server on port 4096. Home Assistant's ingress proxy handles authentication and exposes the interface through the sidebar panel.

Persistent data is stored in the `/data` directory, which survives add-on restarts and updates.
