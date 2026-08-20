# Changelog

## 1.0.0

### Added
- Initial release: [webtmux](https://github.com/chrismccord/webtmux) (a gotty fork with
  tmux-specific features — visual pane layout, window tabs, touch controls, scroll-to-copy-mode),
  built from source and pinned to a specific commit (upstream has no tagged releases).
- Served over ingress only, `--no-auth` (access control is Home Assistant's ingress login, not
  webtmux's own HTTP Basic Auth), single persistent tmux session (`main`) starting in `/config`.
- Full Home Assistant filesystem access (`/config`, `/share`, `/addons`, `/backup`, `/media`)
  plus a persistent shell home directory under `/data`.
