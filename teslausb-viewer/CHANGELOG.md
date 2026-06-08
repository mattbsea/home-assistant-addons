# Changelog

## 0.1.11

### 🐛 Bug Fixes
- Serve the frontend (`index.html` + `/static/*`) with `Cache-Control: no-cache` so the
  browser always revalidates against the ETag. Previously Starlette emitted an ETag but
  no `Cache-Control`, so browsers applied *heuristic* freshness and kept serving the old
  `player.js`/`style.css` after an add-on update — which is why UI changes (e.g. the new
  camera layout) didn't appear without a manual hard-reload. Revalidation is cheap (a 304
  when unchanged) but now every deploy is picked up on the next load.

## 0.1.10

### ✨ Improvements
- **Fixed camera layout.** The player now lays cameras out in a stable 3×2 grid —
  top row `Left Pillar · Front · Right Pillar`, bottom row `Left · Rear · Right` —
  instead of packing them in backend order. Cameras an event doesn't have (e.g. the
  pillar cameras on older hardware) leave their slot blank so the front stays centred.
- **Fits the screen — no scrollbars.** The whole UI now fills the viewport: the player
  scales its camera grid to the available height (no page scroll), and the event grid
  hides its scrollbar chrome while staying swipe/scrollable.
- **New "All" tab** alongside Saved / Sentry / Recent, showing events from every folder
  in one date-sorted grid (the API already supported an unfiltered listing).

## 0.1.9

### 🐛 Bug Fixes
- Fix multi-scene playback stalling when advancing to the next scene. When a scene ended
  (or the Scene button was clicked) the player swapped each camera's `src`, called
  `load()`, then immediately issued `currentTime = 0` and `play()` — but the freshly
  loaded media is still at `readyState 0`, so Safari silently drops both calls: the
  picture freezes and the scrubber stays stuck at the previous scene's end instead of
  resetting to 0:00. The player now waits for the master camera's `canplay` before
  seeking and (re)starting, and resets the seek bar / time label immediately so the
  transport reflects the new scene even when switched while paused.

## 0.1.8

### ✨ Improvements
- Generate poster-frame thumbnails for events with no Tesla `thumb.png` (i.e. RecentClips).
  At scan time the app grabs one frame (~1s in) from the event's front-camera clip with
  `ffmpeg`, scales it down, and stores it in the same thumb cache the `/thumb` endpoint
  already serves — so the grid shows real previews instead of the 📹 placeholder. Eager
  (generated during the scan) and idempotent (skips events already thumbed); work is bounded
  per pass and logged. The browser grid now eagerly loads every card's thumbnail (preload),
  falling back to the placeholder only when none exists yet. Generated thumbnails for events
  that roll out of the index (RecentClips buffer) are pruned automatically.

## 0.1.7

### 🐛 Bug Fixes
- Make RecentClips work end-to-end when clips are nested under a date sub-folder
  (`RecentClips/<date>/<clip>.mp4`) rather than flat. The indexer now lists RecentClips
  **recursively**, and each clip's real remote path is recorded so playback fetches the
  exact file (flattened into the per-event cache) — previously the rolling-buffer scan
  only looked one level deep and the synthetic per-minute "folder" didn't exist on the
  backend, so nested RecentClips both failed to list and couldn't be played. Works for
  flat and nested layouts. Adds a `path` column to the file index (auto-migrated).

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
