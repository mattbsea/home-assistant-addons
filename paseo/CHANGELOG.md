# Changelog

## 1.2.2

### Fixed
- The daemon, terminals and agents ran with `HOME=/home/paseo` (gosu resets `HOME` from
  `/etc/passwd`), an ephemeral directory: `~`, `~/.gitconfig`, `~/.ssh` and tools installed to
  `~/.local/bin` were lost on update. The `paseo` account's home is now `/data/home`.

## 1.2.1

### Added
- `uv`/`uvx` in the image; `uv tool install` tools and uv-managed Pythons persist in the home
  directory.

### Fixed
- DOCS: rtk installs with its curl script (to `~/.local/bin`); `cargo install` needs Rust from
  rustup first. Dropped the `pipx` mention (not installed).

## 1.2.0

### Added
- GitHub CLI (`gh`) from GitHub's apt repository; its login persists under `/data/home/.config/gh`.
- Tools you install yourself from a Paseo terminal now persist and are on `PATH` for terminals,
  agents and login shells: `~/.local/bin` (curl installers, `uv tool`, `pipx`), `~/.cargo/bin`
  (`cargo install`) and `~/.npm-global/bin` (`npm i -g`, via `NPM_CONFIG_PREFIX`).

## 1.1.4

### Changed
- The default "Home Assistant" workspace (`workspace_dir`) is now registered on every start when
  the daemon has no workspaces at all, instead of only once. Removing every project previously
  left "Add project → New directory" with an empty parent picker again.

## 1.1.3

### Fixed
- A browser configured before a reinstall (or a password change) kept a stale saved server it
  could neither use nor fix, because the setup redirect only fired when no hosts were saved.
  The web UI now checks the saved connection for its own address on load: if there is none, the
  daemon rejects its password, or the server id changed, it goes to the password page, which
  replaces the stale entry.

## 1.1.2

### Fixed
- "Add project → New directory" did nothing on a fresh install: its parent-directory picker only
  lists existing workspaces (its search is confined to the daemon's home, which holds only
  dotfiles), so it was empty. On first start the add-on now registers `workspace_dir`
  (`/share/paseo`) as a "Home Assistant" workspace, which then appears as the parent to create
  new project directories in. Typing an absolute path (e.g. `/share/other`) in the picker also
  offers it as a parent.

## 1.1.1

### Changed
- Removed the setup page's "Skip" link: once skipped there was no way back to the password
  prompt. A browser with no saved hosts now always gets the prompt, and the page can be
  reopened any time at `/paseo-setup.html`.

## 1.1.0

### Added
- The web UI now configures itself for this add-on's daemon out of the box. Paseo's web app
  only auto-connects to the daemon that served it when no password is set, so a fresh install
  showed an empty "add host" screen. The image now ships `/paseo-setup.html`, which saves the
  same-origin daemon (endpoint, TLS, password) into the app's saved hosts and loads the app:
  - the sidebar panel opens it with the password (in the URL fragment), so it connects with no
    typing at all;
  - any browser with no saved hosts that opens the web UI is redirected to it and only has to
    enter the password (or pick "Skip").

## 1.0.1

### Fixed
- Paseo terminals and agent shells ran under `/bin/sh` (dash) because `SHELL` was unset;
  it is now `/bin/bash`.

## 1.0.0

### Added
- Initial release, built on the official `ghcr.io/getpaseo/paseo:0.9.2` image (daemon + bundled
  web UI) with the `claude`, `codex` and `opencode` CLIs installed.
- Password protection on by default: an empty `password` option generates a random password on
  first start and writes it back to the add-on's own options via the Supervisor API.
- Sidebar panel (ingress) that frames `external_url`, since Paseo's web app cannot run under
  the ingress path prefix.
- Persistent `/data/home` for Paseo state and agent logins; `/share/paseo` workspace.
- Pinned agent CLIs (claude-code 2.1.281, codex 0.156.1, opencode 1.18.32); `SUPERVISOR_TOKEN` is
  removed from the environment agents inherit.
- Optional `claude_code_oauth_token`, `anthropic_api_key` and `openai_api_key` options.
- No bashio: the upstream image is Debian-based without it, so `run.sh` uses `jq` and `curl`
  directly (same approach as `rustdesk-web`).
