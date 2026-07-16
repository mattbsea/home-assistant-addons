# TeslaUSB Viewer — Local Upload API (drop rclone)

- **Date:** 2026-07-16
- **Status:** Approved design, pending implementation plan
- **Component:** `teslausb-viewer` add-on
- **Target version:** 0.4.0 (breaking config change — drops rclone/S3 entirely)
- **Related:** car-lights M2 (ROCK Pi 4C+ TeslaUSB gadget + archiver) — the archiver is this
  API's only client. This is sub-project 1 of 2; the Pi-side gadget/archiver is designed
  separately once this API's contract is fixed.

## 1. Problem & Context

The add-on currently indexes and streams TeslaCam clips from a remote backend (S3/MinIO/
Drive/SMB/etc.) via `rclone`, per the [streaming-playback
design](2026-06-07-streaming-playback-design.md). That assumed clips arrive at the backend
by some other means (upstream `teslausb`'s own archiver, talking directly to the remote).

We are replacing that upstream archiver with our own (a ROCK Pi 4C+ running a custom USB
gadget + snapshot-based archiver, car-lights M2). That archiver runs on the same physical
host as this add-on (the Home Assistant box), which already has a 4.4 TB USB disk
(`/media/USBDisk`) mounted and used for other add-ons (the GitHub Actions runner). There is
no longer a reason to go through a remote object-storage API at all — the archiver can push
clips straight into a directory on that disk, and the add-on can read them straight back off
it. rclone, the sidecar, the VFS cache, and all S3/remote config become unnecessary
complexity for a same-host writer/reader pair.

**Goal:** the add-on becomes a local-disk-only TeslaCam clip store + viewer: an authenticated
upload endpoint an external device (the Pi archiver) can push files to, and the existing
browse/play UI reading the same tree directly off disk.

### Goals

- Drop rclone, the `serve http` sidecar, the VFS read-through cache, and all S3/remote
  config options — replace with direct reads of a bind-mounted host directory.
- Add an authenticated `PUT` upload endpoint the Pi archiver calls once per clip file,
  writing into the same `SavedClips/SentryClips/RecentClips/<event_dir>/<file>` layout the
  indexer already expects.
- Keep the existing indexer/db schema, event browsing, thumbnailing, and MQTT stats working
  with minimal changes — they should mostly just swap "ask rclone" for "walk local files."
- Expose the add-on on the LAN (not ingress-only) so the Pi, which is not a browser session,
  can reach the upload endpoint.

### Non-Goals

- Multi-backend / remote support of any kind (this is a clean break from the rclone design).
- Changing the upload **format** per clip beyond what's needed (still individual files, not
  archives/zips — matches how Tesla itself writes files, and how the Pi will read them off
  its own snapshot).
- HEVC transcoding, gapless concat — unaffected, unrelated to this change.
- Designing the Pi-side archiver itself (separate spec, once this API is fixed).

## 2. Approach

Bind-mount the host's `/media` into the container (new `map: media:rw`), point the app at a
configurable subpath (`teslacam_path`, default `/media/USBDisk/teslausb`) instead of an
rclone remote. Add one new authenticated route, `PUT /api/upload/{folder}/{event_dir}/
{filename}`, that streams the request body to a temp file and renames it into place — reusing
the folder/filename validation regexes (`FOLDERS`, `EVENT_DIR_RE`, `CLIP_RE`) already defined
in `models.py` for the reader side. Auth is a Home Assistant long-lived access token,
validated by proxying it to HA Core's `/api/` through the Supervisor.

Rather than add a second exposed port for the upload path, the existing ingress port
(`8099`) is also exposed on the LAN (`ports: {"8099/tcp": 8099}`). One FastAPI process, one
port; ingress traffic (browser, no token) hits the UI/read routes, LAN traffic (Pi, bearer
token) hits `/api/upload/*`. The upload route requires the token regardless of how it
arrived; the UI routes are unaffected (ingress already provides its own access control via
the HA frontend session).

**Rejected alternatives:**
- *Second exposed port dedicated to uploads* — works, but means running a second listener
  (two uvicorn binds or a second process) for no real benefit over gating one route by auth.
- *mTLS client cert instead of HA token* — stronger, but needs a CA + cert provisioning step
  on the Pi with no existing infra for it; HA long-lived tokens are already how this
  household issues device credentials.
- *Multipart form upload* — more ceremony (boundary encoding) for no benefit when there's
  exactly one file per request; a raw `PUT` body is simplest for both a Python archiver
  client and `curl -T` during manual testing.

## 3. Architecture

### 3.1 `config.yaml` changes

Remove: `rclone_conf`, `remote_name`, `remote_path`, `s3_endpoint`, `s3_access_key_id`,
`s3_secret_access_key`, `s3_bucket`, `s3_region` (and their `schema` entries).

Add:
```yaml
map:
  - data:rw
  - media:rw          # NEW — host /media, for teslacam_path
homeassistant_api: true   # NEW — needed to validate caller tokens against HA Core
ports:
  "8099/tcp": 8099        # NEW — exposes the existing ingress port on the LAN too
ports_description:
  "8099/tcp": "TeslaCam viewer UI (ingress) + upload API (LAN, token-authenticated)"
options:
  teslacam_path: "/media/USBDisk/teslausb"   # NEW
  refresh_interval_minutes: 30    # unchanged
  cache_size_mb: 2048             # unchanged — now bounds the thumb cache only, see §3.4
  publish_mqtt: true              # unchanged
schema:
  teslacam_path: str
  refresh_interval_minutes: int(5,1440)
  cache_size_mb: int(256,51200)
  publish_mqtt: bool?
```
`hassio_api: true` stays (still used for `bashio::info.timezone`).

### 3.2 Upload endpoint

```
PUT /api/upload/{folder}/{event_dir}/{filename}
Authorization: Bearer <HA long-lived access token>
Body: raw file bytes
```

- `folder` validated against `models.FOLDERS` (`SavedClips`/`SentryClips`/`RecentClips`).
- `event_dir` validated against `models.EVENT_DIR_RE`.
- `filename` validated against `models.CLIP_RE` **or** is exactly `event.json` / `thumb.png`
  (the two non-clip sidecar files `indexer.py` already looks for).
- Any regex mismatch → `400`.
- Write to `<teslacam_path>/<folder>/<event_dir>/<filename>.tmp-<random>`, `fsync`, then
  `os.rename` into place (atomic on the same filesystem — `teslacam_path` is one mount, so
  this always holds). On success → `204`.
- No dedup/overwrite check: a re-PUT of an existing path just replaces it (the Pi archiver
  is responsible for not re-uploading clips it already confirmed, per its own idempotency —
  out of scope here, covered in the Pi-side spec).
- Auth failure → `401`. Missing/unwritable `teslacam_path` → `503` (mirrors today's
  `backend_configured` health signal, now keyed on `Path(teslacam_path).is_dir()` +
  writable, instead of `has_backend()`).

Upload order across a single event's files is **not required** to follow any sequence —
`db.incomplete_event_ids()`'s existing retry logic already tolerates a folder that fills in
over several indexer passes, so the Pi archiver can upload files in whatever order it reads
them off its snapshot.

### 3.3 Auth

New `app/auth.py`: a FastAPI dependency `require_ha_token(request)` that reads the
`Authorization: Bearer` header and calls `GET http://supervisor/core/api/` with that same
header via `httpx` (already a dependency). `200` → pass; anything else (including a network
error reaching the supervisor) → `401`. This call validates the **caller's** token; it does
not use `SUPERVISOR_TOKEN` as the bearer. Applied only to the upload route — UI/read routes
are unauthenticated at the app layer (ingress is HA's access control for those; token auth is
irrelevant there since the browser talking through ingress doesn't have a device token).

### 3.4 Local reads replace rclone

- `app/rclone.py`, `app/stream.py` deleted.
- `app/indexer.py`: `_scan_event_folder`/`_index_event`/`_scan_recent` swap
  `rclone.lsjson`/`rclone.cat` for `os.scandir`/`Path.read_bytes()` walks rooted at
  `settings.teslacam_path`. `CameraFile.path` (stored in `db.files.path`) becomes a path
  relative to `teslacam_path` (was: relative to `remote_base`) — same shape, different root,
  no schema migration needed since it's an opaque TEXT column either way.
- `app/api.py` `/video` handler: resolve `teslacam_path / row["path"]`, guard against path
  traversal (resolved path must stay under `teslacam_path`), serve via `FileResponse` with
  Range support (Starlette's `FileResponse` needs a small wrapper for `Range`/`206` —
  `Content-Range` handling ports over conceptually from the old proxy code, just against a
  local file instead of an upstream socket).
- `app/cache.py`'s `get_thumb()`: local read of `teslacam_path / event_id / "thumb.png"`
  instead of `rclone.cat`. The disk cache this module implements no longer caches *video*
  (nothing to cache — it's already local); `cache_size_mb` now only bounds thumbnail cache
  size, which is a much smaller working set. (Consider lowering the option's practical
  range in a later cleanup; not blocking for this change.)
- `app/thumbnailer.py`: drops the `rclone.copy_files` pull-then-extract step, opens the
  local file directly with ffmpeg.
- `app/main.py`: lifespan no longer starts/stops the `StreamServer` sidecar.

### 3.5 Ownership

`run.sh` currently `chown -R viewer:viewer /data` at startup. Add the same for
`teslacam_path`: `mkdir -p "$TUV_TESLACAM_PATH" && chown -R viewer:viewer
"$TUV_TESLACAM_PATH"`. This add-on is the sole owner/writer of that tree (the Pi archiver
writes over the network via the API, not directly to the filesystem), so recursive chown at
startup is safe and matches the existing `/data` pattern.

## 4. Error Handling

- Upload to a folder/event_dir/filename that fails validation → `400` with the specific
  reason (helps debug the Pi archiver during its own development).
- Upload when `teslacam_path` doesn't exist or isn't writable → `503`, add-on log records it
  at startup (mirrors today's "backend not reachable" startup check, just for local disk).
- Partial write (container killed mid-upload) → the `.tmp-<random>` file is orphaned, never
  renamed into place, so it's invisible to the indexer. A periodic sweep (piggybacked on the
  existing `refresh_interval_minutes` timer) deletes `.tmp-*` files older than one interval.
- Auth: Supervisor unreachable (shouldn't happen — same container network) → `401`, logged
  as a warning distinctly from a bad/expired token, since it indicates an add-on
  misconfiguration rather than a bad caller.

## 5. Testing

- `tests/test_api.py`: replace `fake_rclone.py` fixtures with a temp-directory fixture
  (`tmp_path`) standing in for `teslacam_path`; existing event-listing/detail/thumb/video
  tests get rebased onto it with minimal logic change (they were already asserting on
  `db`/`api` behavior, not on rclone internals).
- New `tests/test_upload.py`: valid upload round-trip (PUT then indexer picks it up), each
  validation rejection (bad folder/event_dir/filename → 400), missing/bad token → 401,
  re-PUT overwrite behavior, `.tmp` sweep behavior.
- New `tests/test_auth.py`: mock the `httpx` call to `http://supervisor/core/api/` for both
  the 200 and non-200 paths.
- Manual verification against the real Supervisor (documented in DOCS.md's troubleshooting
  section) since the auth dependency can't be fully exercised outside a real HA install.

## 6. Documentation

`DOCS.md` and `README.md` get rewritten: drop "Supplying credentials" (S3/rclone.conf
sections) entirely, replace with `teslacam_path` explanation and a short "Pi archiver setup"
pointer (issuing a long-lived access token in HA's user profile, giving it to the archiver).
The "How it works" section changes from "reads through rclone" to "the Pi archiver uploads
clips here directly; this add-on just serves what's on disk."
