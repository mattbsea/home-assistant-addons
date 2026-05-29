# Changelog

## 0.1.1

### 🔒 Security
- Strictly validate the `X-Ingress-Path` header before reflecting it into the page,
  preventing a header-based XSS injection vector.

## 0.1.0

Initial release — vertical slice.

### ✨ Features
- Read any TeslaUSB cloud backend through rclone (paste your existing `rclone.conf`).
- Index **SavedClips** into a SQLite index with incremental, cost-aware scanning.
- Browse events as a thumbnail grid with date filtering and pagination.
- Synchronized multi-camera player (front = master clock; play/pause/seek/speed; per-minute
  "scene" stepper) served from an on-demand local cache with HTTP Range support.
- Statistics published to Home Assistant via MQTT discovery (event counts, last event,
  backend disk usage where supported, …).
- Ingress-only with a sidebar panel.

### 🧭 Roadmap
- SentryClips & RecentClips indexing and filters.
- HEVC → H.264 on-the-fly transcoding fallback for non-Safari browsers.
- Guided first-run setup screen and S3 form.
- Gapless multi-minute playback.
- Multi-architecture builds (aarch64/armv7).
