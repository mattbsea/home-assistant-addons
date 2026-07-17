# Claude Terminal for Home Assistant

This repository contains a custom add-on that integrates Anthropic's Claude Code CLI with Home Assistant.

## Installation

To add this repository to your Home Assistant instance:

1. Go to **Settings** → **Add-ons** → **Add-on Store**
2. Click the three dots menu in the top right corner
3. Select **Repositories**
4. Add the URL: `https://github.com/heytcass/home-assistant-addons`
5. Click **Add**

## Add-ons

### Claude Terminal

A web-based terminal interface with Claude Code CLI pre-installed. This add-on provides a terminal environment directly in your Home Assistant dashboard, allowing you to use Claude's powerful AI capabilities for coding, automation, and configuration tasks.

Features:
- Web terminal access through your Home Assistant UI
- Pre-installed Claude Code CLI that launches automatically
- Direct access to your Home Assistant config directory
- No configuration needed (uses OAuth)
- Access to Claude's complete capabilities including:
  - Code generation and explanation
  - Debugging assistance
  - Home Assistant automation help
  - Learning resources

[Documentation](claude-terminal/DOCS.md)

### Personal AI Infrastructure (PAI)

A Home Assistant add-on for [Daniel Miessler's Personal AI Infrastructure](https://github.com/danielmiessler/Personal_AI_Infrastructure). It clones the PAI repository, installs it, and runs it as a single sidebar panel.

Features:
- One **PAI** sidebar panel with **Pulse** and **Claude Code** tabs
- *PAI Observatory* dashboard, automatically rebuilt to work behind the ingress proxy
- Claude Code web terminal for PAI setup (e.g. the `/interview` wizard)
- One-click setup — no manual install or shell work
- Persistent storage of the PAI repository and state in `/data`
- Optional ElevenLabs voice support

[Documentation](pai/DOCS.md)

### Tesla Fleet Telemetry

Self-hosted [Tesla fleet-telemetry](https://github.com/teslamotors/fleet-telemetry) server. Tesla vehicles stream high-frequency telemetry directly to your Home Assistant host over an encrypted, mutually-authenticated WebSocket, and the server fans those records out to your chosen backends.

Features:
- Runs the official `fleet-telemetry` binary, all server settings exposed on the Configuration page
- First-class backends: Logger (stdout), MQTT (auto-discovers the HA Mosquitto broker), and Google Pub/Sub
- Raw-JSON escape hatch for advanced backends (Kafka, Kinesis, ZMQ)
- mTLS handled by the add-on, with automatic certificate fetch/renew from NGINX Proxy Manager
- Bring-your-own reverse proxy (a TCP passthrough Stream) for public exposure

[Documentation](fleet-telemetry/DOCS.md)

### RustDesk Server

Self-hosted [RustDesk](https://rustdesk.com) in one add-on: the server (`hbbs`/`hbbr`) and a
browser-accessible RustDesk client (via `linuxserver/docker-rustdesk`, streamed with Selkies),
both reachable from the Home Assistant sidebar over ingress.

Features:
- Opens the actual RustDesk desktop client, streamed to your browser — no native install needed
- Runs the official `rustdesk-server` `hbbs`/`hbbr` binaries alongside it, auto-restarted on crash
- Persistent server identity keypair and client settings across restarts and updates
- Optional pre-shared key or encrypted-only enforcement for the server

Note: amd64/arm64 only (no armv7) — see [Documentation](rustdesk/DOCS.md) for known limitations
(shared-memory size, ingress WebSocket load) and what to forward for remote server access.

[Documentation](rustdesk/DOCS.md)

## Community Tools

Tools built by the community to enhance Claude Terminal:

- **[ha-ws-client-go](https://github.com/schoolboyqueue/home-assistant-blueprints/tree/main/scripts/ha-ws-client-go)** by [@schoolboyqueue](https://github.com/schoolboyqueue) - Lightweight Go CLI for Home Assistant WebSocket API. Gives Claude direct access to entity states, service calls, automation traces, and real-time monitoring. Single binary, no dependencies.

- **[Claude Home Assistant Plugins](https://github.com/ESJavadex/claude-homeassistant-plugins)** by [@ESJavadex](https://github.com/ESJavadex) - A collection of Claude Code skills/plugins for Home Assistant, including YAML validation, pre-save hooks, and Lovelace dashboard validation.

- **[Claude Terminal Pro](https://github.com/ESJavadex/claude-code-ha)** by [@ESJavadex](https://github.com/ESJavadex) - A fork with additional features including image paste support, persistent package management, and auto-install configuration.

## Support

If you have any questions or issues with this add-on, please create an issue in this repository.

## Credits

This add-on was created with the assistance of Claude Code itself! The development process, debugging, and documentation were all completed using Claude's AI capabilities.

## License

This repository is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
