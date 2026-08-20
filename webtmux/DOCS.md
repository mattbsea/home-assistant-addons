# WebTmux

A browser-based terminal for [tmux](https://github.com/tmux/tmux), built on
[webtmux](https://github.com/chrismccord/webtmux) (a fork of [gotty](https://github.com/yudai/gotty)
with tmux-specific features): a visual pane-layout sidebar, clickable window tabs, touch-friendly
mobile controls, and scroll-to-copy-mode.

## Installation

1. Add this repository to your Home Assistant Add-on Store.
2. Install **WebTmux**.
3. Start the add-on, then open it from the sidebar panel.

## Usage

Opening the panel attaches to (or creates) a single persistent tmux session named `main`,
starting in `/config`. Reopening the panel, or opening it in a second browser tab, re-attaches to
the same session rather than starting a new one — exactly like reconnecting to tmux over SSH.

There's no configuration and no separate login: access is gated entirely by Home Assistant's own
ingress authentication, so anyone who can open the add-on's panel gets an interactive shell.

Note: the page loads Tailwind CSS and xterm.js from public CDNs client-side, so the *browser*
needs internet access to render it — this doesn't affect the add-on container itself, which has
no such requirement once built.

## Data persistence

- `/data/home`: the shell's `$HOME` — history, dotfiles, anything written there survives add-on
  restarts and updates.
- `/config`, `/share`, `/addons`, `/backup`, `/media`: mounted read-write, same as they'd appear
  over SSH.

The tmux *server* itself is in-memory only — it does not survive an add-on restart. Reopening the
panel after a restart creates a fresh `main` session rather than resuming the old one.

## Security

This add-on grants a full interactive shell with read-write access to your Home Assistant
configuration, add-ons, backups, and media, reachable by anyone who can open its ingress panel.
Only install it if you trust everyone with access to your Home Assistant instance's dashboard.
