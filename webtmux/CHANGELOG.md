# Changelog

## 1.1.0

### Changed
- The terminal now runs as a non-root user instead of root, with passwordless `sudo` available
  for anything that needs root (writing into `/config` and the other root-owned HA volumes).
  New `username` option (default `webtmux`) controls the account name; its persistent home lives
  under `/data/home/<username>`, symlinked to `/home/<username>`.
- The tmux session's default working directory changed from `/config` to `/home/<username>`.

## 1.0.0

### Added
- Initial release: [webtmux](https://github.com/chrismccord/webtmux) (a gotty fork with
  tmux-specific features — visual pane layout, window tabs, touch controls, scroll-to-copy-mode),
  built from source and pinned to a specific commit (upstream has no tagged releases).
- Served over ingress only, `--no-auth` (access control is Home Assistant's ingress login, not
  webtmux's own HTTP Basic Auth), single persistent tmux session (`main`) starting in `/config`.
- Full Home Assistant filesystem access (`/config`, `/share`, `/addons`, `/backup`, `/media`)
  plus a persistent shell home directory under `/data`.
