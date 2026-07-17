# TeslaUSB Viewer: multi-select event delete

**Date:** 2026-07-17
**Status:** Approved

## Problem

The viewer (`teslausb-viewer/`) is read-only today: `app/api.py` exposes list/detail/thumb/video
routes but no way to remove an event. Users have no way to cull footage (free up space on the
TeslaCam storage, or just tidy up) without shelling into the Pi and deleting files by hand. This
adds the ability to select one or more events in the grid and permanently delete them, files and
all, from the viewer itself.

## Scope

- Multi-select UI in the event grid (`app/web/browser.js`), any folder tab including "All".
- A bulk delete API endpoint that removes the underlying video files from disk and drops the
  event from the SQLite index.
- Out of scope: undo/trash, deleting individual clips within an event (whole-event granularity
  only), any change to the upload-port (LAN-facing) surface.

## Deletion semantics — what "delete" means

Deleting an event is **permanent removal of files from disk**, not just hiding it from the index:

- **SavedClips / SentryClips** (`app/indexer.py::EVENT_FOLDERS`): each event owns a real directory
  `teslacam_path/<folder>/<event_dir>/` containing only that event's clips, `thumb.png`, and
  `event.json`. Delete via `shutil.rmtree()` on that directory.
- **RecentClips** (rolling buffer, synthetic per-minute event — `indexer.py::_scan_recent`): an
  event's files are individual clips that may sit in a shared per-date directory alongside other
  events' clips. No directory belongs to a single event, so delete each `CameraFile.path` file
  individually via `Path.unlink()`. Never `rmtree` a RecentClips directory.
- Every path is resolved and checked with the same containment guard `api.py::video()` already
  uses (`resolve()` + `is_relative_to(teslacam_path)`) before any filesystem mutation, so a
  malformed/crafted event_id can never delete outside the TeslaCam root.
- After files are removed: delete the event's row from the `events` table (the `files` table rows
  cascade via the existing `ON DELETE CASCADE` FK — see `app/db.py`), and unlink its cached
  thumbnail at `cache.thumb_path(event_id)` if present.
- Event ids are only ever resolved by looking them up in the DB first (`db.get_event`) — the
  request body never supplies a raw filesystem path.

## Backend API

`POST /api/events/delete`

Request body:
```json
{"event_ids": ["SavedClips/2026-07-16_08-12-03", "RecentClips/2026-07-17_06-40-00"]}
```

Behavior:
- Processes every id in the batch independently — one failing id (already gone, permission
  error, race with a concurrent scan) does not abort the rest. This is a partial-success
  endpoint, not all-or-nothing.
- Response `200`:
  ```json
  {"deleted": ["SavedClips/2026-07-16_08-12-03"],
   "failed": [{"event_id": "RecentClips/2026-07-17_06-40-00", "error": "not found"}]}
  ```
- Lives on the ingress-only router in `app/api.py`, same trust boundary as the existing
  `/api/refresh` — no new auth. (The LAN-facing upload port and its bearer-token auth,
  `app/auth.py`, are untouched — this endpoint has nothing to do with that surface.)

## Frontend — selection mode

`app/web/index.html` / `app/web/app.js` / `app/web/browser.js`:

- A **"Select"** button in the topbar `.actions` group (next to Refresh) toggles selection mode.
- In selection mode:
  - Each `.card`'s `.card-thumb` gets a checkbox overlay in the top-left corner (same corner
    treatment as the existing `.badge` in the bottom-right).
  - Clicking a card toggles its checkbox instead of navigating to the player — the card's link
    click is intercepted while selection mode is active.
  - A selection bar (replacing/augmenting the status bar area) shows:
    `N selected · Select all (X loaded) · Delete selected · Cancel`.
- Selection state is a `Set<event_id>` in `browser.js` module state, alongside the existing
  `offset`/`current`. Works across folder tabs — the "All" tab's mixed Saved/Sentry/Recent cards
  select the same way, since delete only needs each card's `event_id` (which already encodes its
  folder).
- **"Select all"** checks every event currently rendered (i.e. loaded via Load More so far) — it
  does **not** fetch/select events beyond what's on screen.
- Selection clears on: Cancel, folder-tab switch, date-filter change, and after a delete
  completes (success or partial).
- **Delete selected**:
  1. `confirm("Delete N event(s)? This permanently removes the video files from disk.")` — plain
     browser `confirm()`, consistent with the app having no modal library today.
  2. On confirm, `POST /api/events/delete` with the selected ids.
  3. Remove successfully-deleted cards from the DOM directly (no full grid re-fetch, preserves
     scroll position and pagination offset).
  4. Report the result via the existing `window.TUV.status(...)` bar, e.g.
     `"Deleted 4 of 5 event(s) (1 failed)"`.
  5. Any failed ids stay selected (and their cards stay in the grid) so the user can see what
     didn't delete and retry.
  6. Exit selection mode once the call completes.

## Testing

`tests/test_delete.py`, following the existing lightweight `check()`-assertion style used by
`test_api.py` (no pytest fixtures/mocking framework):

- SavedClips event: directory is `rmtree`'d, DB row + its `files` rows gone, cached thumbnail
  file gone.
- RecentClips event: only that event's clip files are removed; a sibling event's clips in the
  same date directory are untouched; DB row gone.
- Partial failure: one real event id + one id that doesn't exist in the index → response has the
  real one in `deleted`, the other in `failed`, and the endpoint still returns `200`.
- Wired into `tests/run.sh` alongside the other test modules.

## Release

- Version bump (0.5.1 → 0.5.2) in `app/__init__.py` and `config.yaml` (kept in lock-step; enforced by an existing test assertion).
- `CHANGELOG.md` entry under a new `## 0.5.2` heading describing the feature.
