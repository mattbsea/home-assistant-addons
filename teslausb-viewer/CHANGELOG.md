# Changelog

## 0.1.6

### ✨ Improvements
- Index **SentryClips** and **RecentClips** in addition to SavedClips. The read path (API,
  DB, UI tabs) already supported all three; the indexer was gated to SavedClips only, so
  backends with only Sentry/Recent footage showed an empty viewer. SavedClips/SentryClips
  are listed once (append-only); RecentClips is re-listed and pruned each pass as a rolling
  buffer. Note: enabling RecentClips means the viewer indexes the entire rolling buffer.

## 0.1.5

### ✨ Improvements
- Make the guided single-line S3 fields (`s3_endpoint`/`s3_access_key_id`/
  `s3_secret_access_key`/`s3_bucket`) a first-class way to configure the backend — they now
  take precedence over a stale auto-written `rclone.conf`, and `s3_bucket` is used as the
  remote path when `remote_path` is empty. Home Assistant options can't render a multi-line
  textarea, so this is the clean single-line path for S3/MinIO; paste `rclone.conf` via the
  config tab's YAML mode for OAuth backends. DOCS updated.

## 0.1.4

### 🐛 Bug Fixes
- Actually fix the configured-backend startup crash loop: the root cause was
  `bashio::config` mangling/aborting on the **multiline** `rclone_conf` string. Read that
  value directly from `options.json` with python instead. (0.1.3 removed `set -e` but the
  multiline read was the real culprit.)

## 0.1.3

### 🐛 Bug Fixes
- Startup crashed (add-on went to "error") once a backend was configured: `set -e` plus
  `set -o pipefail` aborted `run.sh` on a benign non-zero inside a bashio helper. Removed
  the fragile shell options, fixed a latent `[ a ] || [ b ] && c` exit-code bug in the
  guided-S3 branch, and added per-step startup logging.

## 0.1.2

### 🐛 Bug Fixes
- Keep `curl` in the image — bashio uses it to read the Supervisor API, so removing it
  broke all add-on configuration reading (the backend never appeared configured).

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
