# Personal AI Infrastructure (PAI)

A Home Assistant add-on that runs **Pulse**, the Life Dashboard daemon from
[Daniel Miessler's Personal AI Infrastructure](https://github.com/danielmiessler/Personal_AI_Infrastructure).

## What it does

PAI is a "Life Operating System" built around Claude Code. Its web-facing
component is **Pulse** — a unified daemon that serves the *PAI Observatory*
dashboard (agents, skills, telos, security, performance and more).

This add-on:

- Clones the PAI repository on first start (kept in `/data`, persistent)
- Installs it into a Home Assistant-managed location
- Generates a clean, headless-friendly Pulse configuration
- Runs the Pulse daemon and exposes the dashboard through Home Assistant
  ingress (a "PAI Pulse" item appears in the sidebar)

## Installation

1. The add-on lives in this repository — add it to **Settings → Add-ons →
   Add-on Store → ⋮ → Repositories** if you have not already.
2. Install **Personal AI Infrastructure**.
3. Start the add-on and open the **PAI Pulse** panel.

See [DOCS.md](DOCS.md) for configuration options and details.
