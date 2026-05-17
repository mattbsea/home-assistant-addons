# Personal AI Infrastructure (PAI)

A Home Assistant add-on for
[Daniel Miessler's Personal AI Infrastructure](https://github.com/danielmiessler/Personal_AI_Infrastructure).

## What it does

PAI is a "Life Operating System" built around Claude Code. This add-on runs
two parts of it in your browser:

- **Pulse dashboard** — the *PAI Observatory*, served through Home Assistant
  ingress and shown as a **PAI Pulse** sidebar panel.
- **Claude Code terminal** — a password-protected web terminal with the
  Claude Code CLI preinstalled, for PAI setup tasks like the `/interview`
  wizard. Opened via the **Open Web UI** button.

On first start the add-on clones the PAI repository, installs it, rebuilds
the dashboard so it works correctly behind ingress, and starts both services.

## Installation

1. Add this repository to **Settings → Add-ons → Add-on Store → ⋮ →
   Repositories** if you have not already.
2. Install **Personal AI Infrastructure**.
3. Start the add-on (the first start rebuilds the dashboard — give it a few
   minutes), then open the **PAI Pulse** panel.

See [DOCS.md](DOCS.md) for configuration options and details.
