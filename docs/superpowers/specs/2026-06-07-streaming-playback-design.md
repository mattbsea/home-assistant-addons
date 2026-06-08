# TeslaUSB Viewer — On-Demand Streaming Playback (Option A)

- **Date:** 2026-06-07
- **Status:** Approved design, pending implementation plan
- **Component:** `teslausb-viewer` add-on
- **Target version:** 0.2.0 (behaviour-changing playback path)

## 1. Problem & Context

Today, opening an event triggers a blocking **download** of every clip before playback:

1. `POST /api/events/{id}/prepare` → `CacheManager.prepare()` runs `rclone copy`/`copyto`
   to pull the whole event (4–6 cameras × N minutes) into `/data/cache/<key>/`.
2. The frontend polls `GET /api/events/{id}/status` showing "Downloading clips… (x/total)".
3. Only once `state == "ready"` does the player render and serve clips from local disk via
   `FileResponse` (which provides HTTP Range / `206`).

This makes the user wait before the first frame, re-downloads the whole event even if they
only watch ten seconds, and consumes disk for clips that may never be viewed.

**Goal:** stream clips on demand — first frame plays immediately, bytes are fetched from the
backend only as needed, and what is fetched is cached on disk so seeks and the 6-camera
drift re-syncs stay fast.

### Goals

- Remove the upfront "Downloading clips…" gate; playback starts as soon as the player loads.
- Serve any byte range on demand, straight from the backend, with a **read-through disk
  cache** bounded by the existing `cache_size_mb` option.
- Stay **same-origin behind HA ingress** (works off-LAN through existing ingress; no exposed
  ports, no client-side backend access).
- Stay **backend-agnostic** via rclone (S3/MinIO today, but also Drive/Dropbox/SMB/etc.).
- Preserve HTTP Range semantics the multi-camera player depends on (`206`, `Content-Range`,
  `Accept-Ranges`, accurate `Content-Length`/duration).

### Non-Goals

- HEVC → H.264 transcoding (separate roadmap item; unaffected here).
- Gapless multi-minute concat (separate roadmap item).
- Changing the indexer, MQTT stats, thumbnailer, or the event-browser grid.
- FUSE `rclone mount` (needs container privileges the add-on intentionally avoids).

## 2. Approach

Run **one long-lived `rclone serve http` sidecar** rooted at the configured remote, with
rclone's VFS read-through disk cache enabled. The FastAPI `/video` endpoint becomes a thin
**Range-forwarding reverse proxy** to that sidecar on `127.0.0.1`.

rclone already implements chunked read-through caching, LRU/age eviction, and Range serving,
so we write almost no caching logic and inherit correct behaviour for every backend.

**Rejected alternatives** (from brainstorming):
- *Hand-rolled `rclone cat --offset/--count` + custom disk cache* — reinvents the VFS chunk
  cache and eviction; much more code and edge cases.
- *`rclone mount` + VFS* — cleanest API but requires FUSE/privileges; out of bounds.

## 3. Architecture

### 3.1 `rclone serve http` sidecar

Started and supervised by the FastAPI app via its lifespan startup (the app already spawns
rclone subprocesses, and keeping it in Python keeps all config logic next to `Settings`).
`run.sh` is unchanged except where noted in §3.6.

- **Command (conceptual):**
  ```
  rclone --config <rclone_conf> serve http <remote:base>
    --addr 127.0.0.1:<stream_port>
    --vfs-cache-mode full
    --vfs-cache-max-size <cache_size_mb>M
    --vfs-cache-max-age <age>
    --cache-dir <cache_dir>/.vfs
    --read-only
  ```
- **Root = `settings.remote_base()`** (`remote:path`). The HTTP path of a clip is therefore
  its `files.path` column value (already stored relative to `remote_base`; this is exactly
  the subpath `copy_files` feeds to `_target`). No path re-derivation needed.
- **Bind `127.0.0.1` only** — same container, single user; no auth needed and nothing is
  exposed outside the container. The browser never talks to it directly.
- **Port:** a fixed internal port (default `8100`), distinct from the app's ingress port
  `8099`; overridable via a new `TUV_STREAM_PORT` env var for tests/local runs.
- **Lifecycle/supervision:** spawn on startup only when `settings.has_backend()` is true;
  health-check by polling its root; if the process exits unexpectedly, log and respawn with
  a small backoff; terminate it cleanly on app shutdown. If no backend is configured, the
  sidecar is not started and `/video` returns `503`.

### 3.2 `/video` Range-forwarding proxy

`GET /api/events/{event_id}/video/{camera}/{minute_ts}` changes from `FileResponse(localpath)`
to a streaming proxy:

1. `db.find_file(...)` → row; **must now also return `path`** (see §3.4).
2. Build the sidecar URL: `http://127.0.0.1:<stream_port>/<urlquote(row["path"])>`.
3. Open an `httpx.AsyncClient.stream("GET", url, headers=<forwarded Range>)`.
4. Return a `StreamingResponse` that copies the sidecar's **status code** (`200`/`206`),
   and the `Content-Type` (`video/mp4`), `Content-Length`, `Content-Range`, `Accept-Ranges`
   headers verbatim, streaming the body chunks through.
5. Map sidecar `404` → `404`; sidecar unreachable / backend error → `502`/`503`.

A single shared `httpx.AsyncClient` (created in lifespan) is reused across requests. Six
concurrent camera streams plus seek-driven re-requests are normal; rclone's VFS serves them
from cache/backend concurrently.

### 3.3 CacheManager: what changes

- **Removed:** event-clip copy path — `prepare()`, `_copy()`, `status()`, `file_path()`,
  `event_dir()`, and the per-event LRU (`_evict_if_needed` over event dirs). Disk bounding
  for video now belongs to the rclone VFS cache.
- **Kept (unchanged):** the thumbnail subsystem — `thumb_path`, `get_thumb`, `has_thumb`,
  `prune_thumbs`, `thumb_src_dir`, and the `order_cameras` helper. The thumbnailer still
  pulls a single frame via `rclone copy`/`cat`; that is small and orthogonal.
- `total_bytes()` (used by stats) now also reflects the `.vfs` cache dir under `cache_dir`,
  which is fine — it is still "disk used by the viewer."

### 3.4 Database

`Database.find_file` currently selects `camera, minute_ts, filename, size`. Add `path` to the
projection so the proxy can address the clip on the sidecar. The `path` column already exists
(added in 0.1.7, auto-migrated); no schema change. Rows written before 0.1.7 may have a null
`path` — handle by falling back to `"{event_id}/{filename}"` as the sidecar path (the same
shape `copy_to` assumed for legacy folder-style events).

### 3.5 Frontend (`player.js`)

- `open()` no longer calls `prepareThenRender()`. After fetching `/detail`, it renders
  immediately and calls `loadMinute(0)`. `loadMinute` already sets `v.src = vurl(...)` and
  `load()`, so each `<video>` streams directly from `/video`. The 0.1.9 `canplay`-gated
  seek/play fix stays and now matters more (network media, not local disk).
- Remove the "Downloading clips…" preparing UI and the `/status` poll loop (`pollTimer`).
  Keep the initial "Loading event…" spinner until `/detail` resolves.
- `prepareThenRender`, the `status`/`prepare` calls, and `POST /api/events/{id}/prepare` +
  `GET /api/events/{id}/status` endpoints are deleted.

### 3.6 Config / `run.sh`

- New internal setting `stream_port` (env `TUV_STREAM_PORT`, default `8100`); added to
  `Settings`. `run.sh` may export it but a hard default is fine; **no new add-on option**.
- `cache_size_mb` semantics shift from "per-event copy cache cap" to "rclone VFS cache cap."
  Same knob, same intent (disk the viewer may use); DOCS updated to reflect the new meaning.
- Add a VFS cache age bound (e.g. `--vfs-cache-max-age 24h`) so idle bytes are reclaimed.

## 4. Data Flow

```
Browser <video src=/api/.../video/front/<ts>>
  └─ GET (Range: bytes=…) ──▶ FastAPI /video
                                  ├─ db.find_file → remote path
                                  └─ httpx stream ──▶ rclone serve http (127.0.0.1:8100)
                                                          └─ VFS read-through cache ─▶ backend (rclone)
  ◀── 206 + Content-Range + body chunks ◀── streamed back verbatim ──┘
```

First request for a range fetches from the backend and populates the VFS cache; subsequent
overlapping ranges (re-watch, seek-back, drift re-sync) are served from disk.

## 5. Error Handling

| Condition | Behaviour |
|---|---|
| No backend configured | Sidecar not started; `/video` → `503` with a clear message; UI shows an error tile. |
| Sidecar process died | App health-check respawns with backoff; in-flight `/video` → `502` and the player's existing `<video> error` → tile error; retry on next load. |
| Clip not found on backend | Sidecar `404` → `/video` `404` (existing player handles per-tile). |
| Backend slow/unreachable mid-stream | Stream stalls then errors; `<video>` waiting/error handlers already cover it. |
| Legacy null `path` row | Fall back to `"{event_id}/{filename}"` sidecar path. |
| HEVC clip | Unchanged — browser decode error → existing "Can't decode" tile. |

## 6. Testing

The end-to-end API test (`tests/test_api.py` + `tests/fake_rclone.py` + `tests/run.sh`)
currently asserts `prepare`→`status`→`video 200/206/Content-Range`. Update to the streaming
model **without losing real Range coverage**:

- Extend `fake_rclone.py` to implement a `serve http` subcommand: launch a minimal,
  **Range-capable** HTTP server over the fake backend directory tree (custom handler honoring
  `Range:` → `206`/`Content-Range`; full body → `200`). This keeps the test exercising the
  actual proxy + Range path end-to-end against a stand-in sidecar.
- Replace prepare/status assertions with: open detail → `GET /video` returns `200` full
  length, `206` on `Range`, correct `Content-Range`, correct partial body bytes — proxied
  through the app to the fake sidecar.
- Keep all existing non-video assertions (listing, date filter, RecentClips nesting, thumb
  generation, stats, ingress base, MQTT-survives-dead-broker).
- Add: `/video` returns `503` when no backend; legacy null-`path` fallback resolves.

## 7. Rollout

- Single add-on version **0.2.0** (minor bump: playback behaviour changes, prepare/status
  endpoints removed).
- No data migration; existing `/data/cache/<key>/` event dirs from the old model are now
  unused and may be left to age out or removed once on startup (best-effort cleanup).
- DOCS.md: update the playback section and the `cache_size_mb` description.

## 8. Open Questions

- **VFS cache mode:** `full` (cache whole accessed files, best for re-seek) vs `minimal`
  (metadata only). Recommendation: `full`, bounded by `cache_size_mb` + max-age. Revisit if
  disk pressure shows up in practice.
- **`--vfs-read-chunk-size` tuning** for many parallel camera streams — start with rclone
  defaults; tune only if first-frame latency is poor.
