# TeslaUSB Viewer — Local Upload API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `teslausb-viewer` add-on's rclone/S3 remote-backend architecture with a
local bind-mounted directory (`teslacam_path`) plus an authenticated upload endpoint an
external device (the car-lights Pi archiver) can `PUT` clips to.

**Architecture:** `app/config.py`'s `Settings` gains `teslacam_path: Path` and drops every
rclone/remote field. `app/indexer.py`, `app/cache.py`, `app/thumbnailer.py` swap their
`rclone` calls for plain filesystem walks/reads rooted at `teslacam_path`. `app/rangeserve.py`
(new) replaces `app/stream.py`'s rclone-proxy with direct local-file Range serving.
`app/auth.py` (new) validates a caller's Home Assistant long-lived access token by proxying it
to HA Core through the Supervisor. `app/upload.py` (new) is the `PUT` endpoint, token-gated,
writing atomically (temp file + rename) into the same `SavedClips/SentryClips/RecentClips`
layout the indexer already expects.

**Tech Stack:** Python 3.11, FastAPI, `httpx` (already a dependency), stdlib `os`/`pathlib`/
`shutil` for all filesystem work. No new third-party dependencies.

## Global Constraints

- Drop rclone/S3/remote-backend support entirely — no config option, code path, or process
  may reference `rclone` after this plan is complete.
- The upload endpoint is `PUT /api/upload/{folder}/{event_dir}/{filename}`, authenticated by
  a bearer Home Assistant long-lived access token validated via
  `GET http://supervisor/core/api/` (requires `homeassistant_api: true` in `config.yaml`).
- Folder/event-dir/filename validation reuses `models.FOLDERS`, `models.EVENT_DIR_RE`,
  `models.CLIP_RE` — the exact patterns the indexer already relies on.
- Writes are atomic: temp file in the destination directory, then `os.replace()`.
- `teslacam_path` config option, default `/media/USBDisk/teslausb`; the add-on's `map` must
  include `media:rw`; the add-on chowns `teslacam_path` to the `viewer` user at startup (it
  is the tree's sole owner).
- The add-on exposes port `8099` on the LAN (in addition to ingress) so the Pi — not a
  browser session — can reach the upload endpoint.
- Follow this repo's existing test convention: standalone `python tests/test_*.py` scripts
  using plain `assert` or a hand-rolled `check()` helper, run via `tests/run.sh` (**not**
  pytest — there is no pytest in `requirements.txt` and none should be added).
- Version bump to `0.4.0` (breaking config change) in both `config.yaml` and
  `app/__init__.py.__version__` — `tests/test_api.py` already asserts these two stay in sync.

---

### Task 1: Local-disk config (`app/config.py`, `app/models.py`)

**Files:**
- Modify: `teslausb-viewer/app/config.py`
- Modify: `teslausb-viewer/app/models.py:48`
- Test: `teslausb-viewer/tests/test_config.py` (new)

**Interfaces:**
- Consumes: nothing (foundational).
- Produces: `Settings.teslacam_path: Path`, `Settings.has_backend() -> bool` (now `True` iff
  `teslacam_path.is_dir()`). Every later task's `Settings` usage relies on these two names.
  `Settings.db_path` unchanged. Removed: `rclone_conf`, `remote_name`, `remote_path`,
  `stream_port`, `resolved_remote_name()`, `remote_base()`.

- [ ] **Step 1: Write the failing test**

```python
# teslausb-viewer/tests/test_config.py
"""Unit checks for Settings — local teslacam_path replaces the rclone remote fields."""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def run():
    work = "/tmp/tuv-test-config"
    teslacam = os.path.join(work, "teslacam")
    os.environ["TUV_DATA_DIR"] = work
    os.environ["TUV_TESLACAM_PATH"] = teslacam

    from app.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    assert s.teslacam_path == Path(teslacam), s.teslacam_path
    assert s.has_backend() is False, "nonexistent dir must report no backend"

    os.makedirs(teslacam, exist_ok=True)
    get_settings.cache_clear()
    s2 = get_settings()
    assert s2.has_backend() is True

    assert not hasattr(s2, "remote_name"), "remote_name must be removed"
    assert not hasattr(s2, "rclone_conf"), "rclone_conf must be removed"

    print("PASS config teslacam_path + has_backend")


if __name__ == "__main__":
    run()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd teslausb-viewer && PYTHONPATH=. python3 tests/test_config.py`
Expected: `AttributeError` (or similar) — `Settings` has no `teslacam_path` yet.

- [ ] **Step 3: Rewrite `app/config.py`**

```python
"""Runtime settings, loaded from the TUV_* environment variables exported by run.sh.

run.sh is the single source of truth for configuration: it reads the add-on options
via bashio and hands them to the app as environment variables. The app never parses
/data/options.json directly, so it behaves identically under the Supervisor and under a
plain `podman run` that sets the same variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _clean(value: str | None) -> str:
    """Treat bashio's empty/"null" sentinels as unset."""
    if value is None:
        return ""
    value = value.strip()
    return "" if value == "null" else value


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    teslacam_path: Path
    cache_dir: Path
    refresh_minutes: int
    cache_size_mb: int
    port: int
    mqtt_enabled: bool
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str
    mqtt_password: str

    @property
    def db_path(self) -> Path:
        return self.data_dir / "index.db"

    def has_backend(self) -> bool:
        return self.teslacam_path.is_dir()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    data_dir = Path(os.environ.get("TUV_DATA_DIR", "/data"))
    return Settings(
        data_dir=data_dir,
        teslacam_path=Path(os.environ.get("TUV_TESLACAM_PATH", "/media/USBDisk/teslausb")),
        cache_dir=Path(os.environ.get("TUV_CACHE_DIR", str(data_dir / "cache"))),
        refresh_minutes=max(5, _int("TUV_REFRESH_MINUTES", 30)),
        cache_size_mb=max(256, _int("TUV_CACHE_SIZE_MB", 2048)),
        port=_int("TUV_PORT", 8099),
        mqtt_enabled=os.environ.get("TUV_MQTT_ENABLED", "false").lower() == "true",
        mqtt_host=_clean(os.environ.get("TUV_MQTT_HOST")),
        mqtt_port=_int("TUV_MQTT_PORT", 1883),
        mqtt_username=_clean(os.environ.get("TUV_MQTT_USERNAME")),
        mqtt_password=_clean(os.environ.get("TUV_MQTT_PASSWORD")),
    )
```

- [ ] **Step 4: Update the stale comment in `app/models.py`**

In `teslausb-viewer/app/models.py:48`, change:
```python
    path: str = ""  # remote subpath under remote_base (handles nested layouts like RecentClips/<date>/)
```
to:
```python
    path: str = ""  # local subpath under teslacam_path (handles nested layouts like RecentClips/<date>/)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd teslausb-viewer && PYTHONPATH=. python3 tests/test_config.py`
Expected: `PASS config teslacam_path + has_backend`, exit 0.

- [ ] **Step 6: Commit**

```bash
cd teslausb-viewer
git add app/config.py app/models.py tests/test_config.py
git commit -m "feat(teslausb-viewer): local teslacam_path replaces rclone remote config"
```

---

### Task 2: Local filesystem indexer (`app/indexer.py`)

**Files:**
- Modify: `teslausb-viewer/app/indexer.py`
- Test: `teslausb-viewer/tests/test_indexer.py` (new)

**Interfaces:**
- Consumes: `Settings.teslacam_path`, `Settings.has_backend()` (Task 1); `Database` (unchanged,
  `app/db.py`); `CLIP_RE`, `EVENT_DIR_RE`, `CameraFile`, `Event`, `event_id`,
  `parse_event_timestamp`, `utc_now_iso` from `app/models.py` (unchanged).
- Produces: `Indexer.scan() -> dict` — same shape as before (`{"added": int, "skipped": bool}`
  or `{"scanned": 0, "added": 0, "skipped": True}`), so `app/main.py`'s `refresh_and_publish`
  needs no change for this task.

- [ ] **Step 1: Write the failing test**

```python
# teslausb-viewer/tests/test_indexer.py
"""Unit checks for the local-disk Indexer (no HTTP, no rclone)."""
import asyncio
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _write_sample_tree(root):
    ev = os.path.join(root, "SavedClips", "2024-01-15_10-30-22")
    os.makedirs(ev, exist_ok=True)
    with open(os.path.join(ev, "event.json"), "w") as f:
        f.write('{"reason":"user_interaction_honk","city":"Seattle","est_lat":47.6,"est_lon":-122.3}')
    with open(os.path.join(ev, "thumb.png"), "wb") as f:
        f.write(b"PNGDATA")
    with open(os.path.join(ev, "2024-01-15_10-30-22-front.mp4"), "wb") as f:
        f.write(os.urandom(4096))
    with open(os.path.join(ev, "2024-01-15_10-30-22-back.mp4"), "wb") as f:
        f.write(os.urandom(4096))

    rec = os.path.join(root, "RecentClips", "2024-01-15")
    os.makedirs(rec, exist_ok=True)
    with open(os.path.join(rec, "2024-01-15_10-31-00-front.mp4"), "wb") as f:
        f.write(os.urandom(2048))
    with open(os.path.join(rec, "2024-01-15_10-31-00-back.mp4"), "wb") as f:
        f.write(os.urandom(2048))


def run():
    work = tempfile.mkdtemp()
    try:
        teslacam = os.path.join(work, "teslacam")
        os.makedirs(teslacam)
        _write_sample_tree(teslacam)

        os.environ["TUV_TESLACAM_PATH"] = teslacam
        os.environ["TUV_DATA_DIR"] = work
        os.environ["TUV_CACHE_DIR"] = os.path.join(work, "cache")

        from app.config import get_settings
        get_settings.cache_clear()
        from app.db import Database
        from app.indexer import Indexer

        settings = get_settings()
        db = Database(settings.db_path)
        indexer = Indexer(settings, db)

        result = asyncio.run(indexer.scan())
        assert result["added"] >= 2, result  # SavedClips event + RecentClips minute

        row = db.get_event("SavedClips/2024-01-15_10-30-22")
        assert row is not None
        assert row["reason"] == "user_interaction_honk", row
        assert row["city"] == "Seattle", row
        assert row["thumb_present"] == 1, row
        assert len(row["files"]) == 2, row["files"]
        paths = {f["path"] for f in row["files"]}
        assert paths == {
            "SavedClips/2024-01-15_10-30-22/2024-01-15_10-30-22-front.mp4",
            "SavedClips/2024-01-15_10-30-22/2024-01-15_10-30-22-back.mp4",
        }, paths

        rec_row = db.get_event("RecentClips/2024-01-15_10-31-00")
        assert rec_row is not None
        rec_paths = {f["path"] for f in rec_row["files"]}
        assert rec_paths == {
            "RecentClips/2024-01-15/2024-01-15_10-31-00-front.mp4",
            "RecentClips/2024-01-15/2024-01-15_10-31-00-back.mp4",
        }, rec_paths

        db.close()
        print("PASS local indexer scan")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    run()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd teslausb-viewer && PYTHONPATH=. python3 tests/test_indexer.py`
Expected: fails importing/calling `rclone` (indexer.py still imports the old module) or an
`AttributeError`/`ModuleNotFoundError`.

- [ ] **Step 3: Rewrite `app/indexer.py`**

```python
"""Incremental scan of the local TeslaCam directory into the SQLite index.

Cost control is the whole point: listing an event folder's immediate subdirectories is cheap,
but listing every file in every event on every refresh is not. Archived Saved/Sentry events
are append-only, so we list a folder's files only the first time we see it. RecentClips is a
rolling buffer, so it is re-listed and pruned each pass. All three folders
(SavedClips, SentryClips, RecentClips) are scanned — see SCAN_FOLDERS.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from .config import Settings
from .db import Database
from .models import (
    CLIP_RE,
    EVENT_DIR_RE,
    CameraFile,
    Event,
    event_id,
    parse_event_timestamp,
    utc_now_iso,
)

log = logging.getLogger("teslausb_viewer.indexer")

# Scan all three TeslaUSB folders. SavedClips/SentryClips are append-only event dirs
# (listed once); RecentClips is a rolling buffer (re-listed and pruned each pass).
SCAN_FOLDERS = ("SavedClips", "SentryClips", "RecentClips")
EVENT_FOLDERS = ("SavedClips", "SentryClips")


def _iter_dirs(path: Path) -> list[str]:
    """Sorted subdirectory names directly under `path`. Empty list if `path` is absent."""
    try:
        return sorted(e.name for e in os.scandir(path) if e.is_dir())
    except FileNotFoundError:
        return []


def _iter_files_recursive(path: Path) -> list[tuple[str, str, int]]:
    """(basename, path relative to `path`, size) for every file under `path`, recursively."""
    out = []
    for root, _dirnames, filenames in os.walk(path):
        rel_root = os.path.relpath(root, path)
        for name in sorted(filenames):
            full = os.path.join(root, name)
            rel = name if rel_root == "." else os.path.normpath(os.path.join(rel_root, name))
            out.append((name, rel, os.path.getsize(full)))
    return out


def _build_files(entries: list[tuple[str, str, int]], parent: str) -> list[CameraFile]:
    """Turn (basename, relative-path, size) tuples into CameraFiles, recording each clip's
    full path relative to teslacam_path (`parent/<relative path>`)."""
    files = []
    for name, rel, size in entries:
        m = CLIP_RE.match(name)
        if not m:
            continue
        files.append(
            CameraFile(
                camera=m.group("camera"),
                minute_ts=m.group("minute"),
                filename=name,
                size=size,
                path=f"{parent}/{rel}".strip("/"),
            )
        )
    return files


def _parse_event_json(raw: bytes) -> dict:
    try:
        data = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return {}
    lat = data.get("est_lat")
    lon = data.get("est_lon")
    return {
        "reason": data.get("reason"),
        "city": data.get("city"),
        "est_lat": float(lat) if _is_number(lat) else None,
        "est_lon": float(lon) if _is_number(lon) else None,
    }


def _is_number(value) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


class Indexer:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self._lock = asyncio.Lock()

    async def scan(self) -> dict:
        """Run one incremental scan pass. Safe to call concurrently (serialised by a lock)."""
        async with self._lock:
            if not self.settings.has_backend():
                log.info("teslacam_path not present — skipping scan")
                return {"scanned": 0, "added": 0, "skipped": True}
            added = 0
            for folder in SCAN_FOLDERS:
                if folder in EVENT_FOLDERS:
                    added += await asyncio.to_thread(self._scan_event_folder, folder)
                else:
                    added += await asyncio.to_thread(self._scan_recent, folder)
            self.db.set_meta("last_index_refresh", utc_now_iso())
            log.info("Scan complete: %d new event(s) indexed", added)
            return {"added": added, "skipped": False}

    def _scan_event_folder(self, folder: str) -> int:
        dirs = _iter_dirs(self.settings.teslacam_path / folder)
        known = self.db.known_event_ids(folder)
        incomplete = self.db.incomplete_event_ids(folder)
        now = utc_now_iso()
        added = 0
        for name in dirs:
            if not EVENT_DIR_RE.match(name):
                continue
            eid = event_id(folder, name)
            # Skip events already fully indexed; re-list new or mid-upload (incomplete) ones.
            if eid in known and eid not in incomplete:
                continue
            self._index_event(folder, name, eid, now)
            if eid not in known:
                added += 1
        return added

    def _index_event(self, folder: str, name: str, eid: str, now: str) -> None:
        ts = parse_event_timestamp(name)
        subpath = f"{folder}/{name}"
        event_dir = self.settings.teslacam_path / subpath
        entries = (
            [(e.name, e.name, e.stat().st_size) for e in os.scandir(event_dir) if e.is_file()]
            if event_dir.is_dir()
            else []
        )
        names = {n for n, _, _ in entries}
        event = Event(
            event_id=eid,
            folder=folder,
            event_ts=ts.isoformat() if ts else name,
            thumb_present="thumb.png" in names,
            files=_build_files(entries, subpath),
        )
        if "event.json" in names:
            try:
                raw = (event_dir / "event.json").read_bytes()
                event_meta = _parse_event_json(raw)
                event.reason = event_meta["reason"]
                event.city = event_meta["city"]
                event.est_lat = event_meta["est_lat"]
                event.est_lon = event_meta["est_lon"]
            except OSError as exc:
                log.info("No readable event.json for %s: %s", subpath, exc)
        self.db.upsert_event(event, first_seen=now, now=now)

    def _scan_recent(self, folder: str) -> int:
        """RecentClips: rolling buffer of clips grouped into synthetic per-minute events.

        Walked recursively so it works whether clips sit flat in RecentClips/ (stock
        TeslaUSB) or under a date sub-folder like RecentClips/<date>/.
        """
        entries = _iter_files_recursive(self.settings.teslacam_path / folder)
        files = _build_files(entries, folder)
        by_minute: dict[str, list] = {}
        for f in files:
            by_minute.setdefault(f.minute_ts, []).append(f)
        now = utc_now_iso()
        keep, added = set(), 0
        known = self.db.known_event_ids(folder)
        for minute, group in by_minute.items():
            eid = event_id(folder, minute)
            keep.add(eid)
            ts = parse_event_timestamp(minute)
            event = Event(
                event_id=eid, folder=folder,
                event_ts=ts.isoformat() if ts else minute, files=group,
            )
            self.db.upsert_event(event, first_seen=now, now=now)
            if eid not in known:
                added += 1
        self.db.delete_events_not_in(folder, keep)
        return added
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd teslausb-viewer && PYTHONPATH=. python3 tests/test_indexer.py`
Expected: `PASS local indexer scan`, exit 0.

- [ ] **Step 5: Commit**

```bash
cd teslausb-viewer
git add app/indexer.py tests/test_indexer.py
git commit -m "feat(teslausb-viewer): index teslacam_path directly, no rclone"
```

---

### Task 3: Local thumbnail + Range video serving, drop the rclone sidecar

**Files:**
- Create: `teslausb-viewer/app/rangeserve.py`
- Modify: `teslausb-viewer/app/cache.py`
- Modify: `teslausb-viewer/app/thumbnailer.py`
- Modify: `teslausb-viewer/app/api.py:126-137` (the `/video` handler)
- Modify: `teslausb-viewer/app/main.py` (remove `StreamServer` wiring)
- Test: `teslausb-viewer/tests/test_video_thumb.py` (new)

**Interfaces:**
- Consumes: `Settings.teslacam_path` (Task 1); `Indexer.scan()` (Task 2, used transitively via
  `/api/refresh`); `CacheManager` (this task's rewrite); `CAMERAS` from `app/models.py`
  (unchanged).
- Produces: `serve_file_range(path: Path, range_header: str | None) -> Response` in
  `app/rangeserve.py` — the only thing later tasks need from this module.
  `CacheManager.get_thumb(event_id: str) -> bytes | None` keeps its exact signature.

- [ ] **Step 1: Write the failing test**

```python
# teslausb-viewer/tests/test_video_thumb.py
"""End-to-end check: local thumbnail + Range video serving (no rclone, no upload yet)."""
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _write_sample_tree(root):
    ev = os.path.join(root, "SavedClips", "2024-01-15_10-30-22")
    os.makedirs(ev, exist_ok=True)
    with open(os.path.join(ev, "thumb.png"), "wb") as f:
        f.write(b"PNGDATA")
    with open(os.path.join(ev, "2024-01-15_10-30-22-front.mp4"), "wb") as f:
        f.write(os.urandom(4096))


def run():
    work = tempfile.mkdtemp()
    try:
        teslacam = os.path.join(work, "teslacam")
        os.makedirs(teslacam)
        _write_sample_tree(teslacam)

        os.environ["TUV_TESLACAM_PATH"] = teslacam
        os.environ["TUV_DATA_DIR"] = work
        os.environ["TUV_CACHE_DIR"] = os.path.join(work, "cache")
        os.environ["TUV_MQTT_ENABLED"] = "false"

        from app.config import get_settings
        get_settings.cache_clear()

        from fastapi.testclient import TestClient
        import app.main as m

        failures = []

        def check(name, cond, extra=""):
            print(("PASS" if cond else "FAIL"), name, extra)
            if not cond:
                failures.append(name)

        with TestClient(m.app) as c:
            r = c.post("/api/refresh")
            check("refresh 200", r.status_code == 200, str(r.json()))

            r = c.get("/api/events/SavedClips/2024-01-15_10-30-22/thumb")
            check("thumb served", r.status_code == 200 and r.content == b"PNGDATA")

            r = c.get("/api/events/SavedClips/2024-01-15_10-30-22/video/front/2024-01-15_10-30-22")
            check("video 200", r.status_code == 200, str(r.status_code))
            check("video full length 4096", len(r.content) == 4096, str(len(r.content)))

            r = c.get(
                "/api/events/SavedClips/2024-01-15_10-30-22/video/front/2024-01-15_10-30-22",
                headers={"Range": "bytes=0-1023"},
            )
            check("video 206 on range", r.status_code == 206, str(r.status_code))
            check("content-range header",
                  r.headers.get("content-range", "").startswith("bytes 0-1023/4096"))
            check("range body 1024 bytes", len(r.content) == 1024, str(len(r.content)))

            # Legacy rows with an empty path fall back to event_id/filename and still stream.
            _db = sqlite3.connect(get_settings().db_path)
            _db.execute(
                "UPDATE files SET path='' WHERE event_id=? AND camera='front'",
                ("SavedClips/2024-01-15_10-30-22",),
            )
            _db.commit()
            _db.close()
            r = c.get("/api/events/SavedClips/2024-01-15_10-30-22/video/front/2024-01-15_10-30-22")
            check("legacy empty-path still streams", r.status_code == 200, str(r.status_code))

        print()
        print("RESULT:", "ALL PASS" if not failures else f"{len(failures)} FAILURE(S): {failures}")
        if failures:
            raise SystemExit(1)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    run()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd teslausb-viewer && PYTHONPATH=. python3 tests/test_video_thumb.py`
Expected: fails at app startup (`main.py` still tries to start the rclone `StreamServer`
sidecar, which errors with no `rclone` binary / no backend configured in this test env) or a
404/503 on the thumb/video checks.

- [ ] **Step 3: Create `app/rangeserve.py`**

```python
# teslausb-viewer/app/rangeserve.py
"""Serve a local file with HTTP Range support (bytes Range -> 206 partial content).

Video playback needs seek, which requires Range requests. Starlette's FileResponse doesn't
implement Range, so this is a small, self-contained replacement for the old rclone-proxy
video_response() in the now-deleted stream.py.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse

CHUNK_SIZE = 1024 * 1024


def serve_file_range(path: Path, range_header: str | None) -> Response:
    if not path.is_file():
        raise HTTPException(404, "clip not found")
    size = path.stat().st_size
    if not range_header or not range_header.startswith("bytes="):
        def _full():
            with open(path, "rb") as fh:
                while chunk := fh.read(CHUNK_SIZE):
                    yield chunk

        return StreamingResponse(
            _full(), status_code=200,
            headers={"content-type": "video/mp4", "accept-ranges": "bytes",
                     "content-length": str(size)},
        )

    start_s, _, end_s = range_header[len("bytes="):].partition("-")
    try:
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else size - 1
    except ValueError:
        raise HTTPException(416, "invalid range")
    end = min(end, size - 1)
    if start > end or start < 0:
        raise HTTPException(416, "invalid range")
    length = end - start + 1

    def _ranged():
        with open(path, "rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        _ranged(), status_code=206,
        headers={
            "content-type": "video/mp4",
            "accept-ranges": "bytes",
            "content-range": f"bytes {start}-{end}/{size}",
            "content-length": str(length),
        },
    )
```

- [ ] **Step 4: Rewrite `CacheManager` in `app/cache.py`**

```python
# teslausb-viewer/app/cache.py
"""Thumbnail cache for events (Tesla-supplied `thumb.png` or an ffmpeg-generated frame).

Video is served directly off local disk (app/rangeserve.py) — no caching needed for it. This
module only owns the on-disk thumbnail cache and the camera-ordering helper used by the
player grid.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .config import Settings
from .models import CAMERAS

log = logging.getLogger("teslausb_viewer.cache")

THUMB_DIR_NAME = ".thumbs"


def _key(event_id: str) -> str:
    return event_id.replace("/", "__")


class CacheManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.cache_dir
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / THUMB_DIR_NAME).mkdir(exist_ok=True)

    # --- thumbnails ---------------------------------------------------------
    def key(self, event_id: str) -> str:
        """Cache-safe key for an event id (shared with the thumbnailer)."""
        return _key(event_id)

    def thumb_path(self, event_id: str) -> Path:
        """On-disk location of an event's thumbnail (Tesla-fetched or ffmpeg-generated)."""
        return self.root / THUMB_DIR_NAME / f"{_key(event_id)}.png"

    def has_thumb(self, event_id: str) -> bool:
        return self.thumb_path(event_id).is_file()

    def prune_thumbs(self, valid_keys: set[str]) -> None:
        """Delete cached thumbnails whose event key is no longer present in the index."""
        thumbs = self.root / THUMB_DIR_NAME
        if not thumbs.is_dir():
            return
        for png in thumbs.glob("*.png"):
            if png.stem not in valid_keys:
                try:
                    png.unlink()
                except OSError:
                    pass

    async def get_thumb(self, event_id: str) -> bytes | None:
        """Return thumbnail bytes, caching the Tesla thumb on first request. None if absent.

        A previously generated frame (thumbnailer) lives at the same path, so it is served
        here too without re-reading the source clip.
        """
        cached = self.thumb_path(event_id)
        if cached.is_file():
            return await asyncio.to_thread(cached.read_bytes)
        src = self.settings.teslacam_path / event_id / "thumb.png"
        if not await asyncio.to_thread(src.is_file):
            return None
        data = await asyncio.to_thread(src.read_bytes)
        if data:
            cached.write_bytes(data)
        return data or None


def order_cameras(cameras: list[str]) -> list[str]:
    """Stable camera ordering for the player grid (known order, then any extras)."""
    known = [c for c in CAMERAS if c in cameras]
    extra = [c for c in cameras if c not in CAMERAS]
    return known + extra
```

- [ ] **Step 5: Update `app/thumbnailer.py`'s `_generate` (drop the copy-then-extract pull)**

Replace the whole `_generate` function (and drop the now-unused `rclone` import) with:

```python
async def _generate(settings: Settings, cache: CacheManager, event_id: str, files: list[dict]) -> bool:
    clip = _pick_clip(files)
    if not clip:
        return False
    src = settings.teslacam_path / clip["path"]
    dest = cache.thumb_path(event_id)
    if await _extract_frame(src, dest):
        log.info("Generated thumbnail for %s (from %s)", event_id, clip["camera"])
        return True
    return False
```

At the top of `teslausb-viewer/app/thumbnailer.py`, remove the line `from . import rclone`
(no longer used) and update the module docstring's second paragraph from "we grab one frame
... with ffmpeg" to note the clip is read directly off `teslacam_path` (no network pull).
`_pick_clip` and `_extract_frame` are unchanged — they already operate on `Path` objects.

- [ ] **Step 6: Update the `/video` handler in `app/api.py`**

Add the import at the top:
```python
from .rangeserve import serve_file_range
```

Replace the `video` function (currently `api.py:126-137`):

```python
@router.get("/api/events/{event_id:path}/video/{camera}/{minute_ts}")
async def video(event_id: str, camera: str, minute_ts: str, request: Request) -> Response:
    st = _state(request)
    row = await asyncio.to_thread(st.db.find_file, event_id, camera, minute_ts)
    if not row:
        raise HTTPException(404, "no such clip")
    # `path` is the clip's location under teslacam_path (recorded since 0.1.7); older rows
    # fall back to the folder-shaped path the copy model assumed.
    rel_path = row.get("path") or f"{event_id}/{row['filename']}"
    local_path = (st.settings.teslacam_path / rel_path).resolve()
    root = st.settings.teslacam_path.resolve()
    if not local_path.is_relative_to(root):
        raise HTTPException(404, "no such clip")
    return serve_file_range(local_path, request.headers.get("range"))
```

Also update `api.py`'s module docstring (currently says "Video is streamed on demand: /video
proxies the browser's Range request to the rclone serve http sidecar") to instead say
"/video serves the clip directly off local disk with Range support (see app/rangeserve.py)".

- [ ] **Step 7: Remove the `StreamServer` wiring from `app/main.py`**

Delete the import `from .stream import StreamServer`.

In `lifespan()`, delete these two lines:
```python
        app.state.stream = StreamServer(settings)
        await app.state.stream.start()
```

And delete this line from the `finally` block:
```python
        await app.state.stream.stop()
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd teslausb-viewer && PYTHONPATH=. python3 tests/test_video_thumb.py`
Expected: `RESULT: ALL PASS`, exit 0.

- [ ] **Step 9: Commit**

```bash
cd teslausb-viewer
git add app/rangeserve.py app/cache.py app/thumbnailer.py app/api.py app/main.py tests/test_video_thumb.py
git commit -m "feat(teslausb-viewer): serve thumbnails/video from local disk, drop rclone sidecar"
```

---

### Task 4: Stats without rclone (`app/stats.py`)

**Files:**
- Modify: `teslausb-viewer/app/stats.py`
- Modify: `teslausb-viewer/tests/test_video_thumb.py` (extend)

**Interfaces:**
- Consumes: `Settings.teslacam_path`, `Settings.has_backend()` (Task 1).
- Produces: `compute(settings, db) -> dict` — same key names as before
  (`backend_used_bytes`/`backend_free_bytes`/`backend_total_bytes`, now sourced from
  `shutil.disk_usage(teslacam_path)` instead of `rclone about`), so the MQTT publisher and
  `/api/stats` need no changes.

- [ ] **Step 1: Extend the test**

In `teslausb-viewer/tests/test_video_thumb.py`, inside the `with TestClient(m.app) as c:`
block, after the `refresh 200` check, add:

```python
            s = c.get("/api/stats").json()
            check("stats backend_total_bytes present", isinstance(s.get("backend_total_bytes"), int),
                  str(s.get("backend_total_bytes")))
            check("stats total >= used", s["backend_total_bytes"] >= s["backend_used_bytes"],
                  str(s))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd teslausb-viewer && PYTHONPATH=. python3 tests/test_video_thumb.py`
Expected: `FAIL stats backend_total_bytes present None` (stats.py still calls `rclone.about`,
which returns `None` since there's no rclone config in this test env).

- [ ] **Step 3: Rewrite `app/stats.py`**

```python
"""Compute the statistics surfaced to Home Assistant (and the in-UI stats endpoint)."""

from __future__ import annotations

import shutil
from datetime import datetime

from .config import Settings
from .db import Database


async def compute(settings: Settings, db: Database) -> dict:
    base = await _from_db(db)
    disk = _disk_usage(settings) if settings.has_backend() else None
    base["backend_used_bytes"] = (disk or {}).get("used")
    base["backend_free_bytes"] = (disk or {}).get("free")
    base["backend_total_bytes"] = (disk or {}).get("total")
    return base


def _disk_usage(settings: Settings) -> dict | None:
    try:
        usage = shutil.disk_usage(settings.teslacam_path)
    except OSError:
        return None
    return {"total": usage.total, "used": usage.used, "free": usage.free}


async def _from_db(db: Database) -> dict:
    import asyncio

    s = await asyncio.to_thread(db.stats)
    per_folder = s["per_folder"]
    last_refresh = await asyncio.to_thread(db.get_meta, "last_index_refresh")

    saved = per_folder.get("SavedClips", 0)
    sentry = per_folder.get("SentryClips", 0)
    recent = per_folder.get("RecentClips", 0)

    return {
        "total_events": saved + sentry + recent,
        "savedclips_count": saved,
        "sentryclips_count": sentry,
        "recentclips_count": recent,
        "total_video_files": s["total_files"],
        "last_event": _to_iso(s["last_event_ts"]),
        "today_sentry_count": await asyncio.to_thread(_today_sentry, db),
        "last_index_refresh": last_refresh,
    }


def _today_sentry(db: Database) -> int:
    """Sentry events whose local-time date is today (TZ comes from the container env)."""
    today = datetime.now().date().isoformat()
    rows, _ = db.list_events(
        folder="SentryClips", date_from=today, date_to=None, limit=10_000, offset=0
    )
    return len(rows)


def _to_iso(event_ts: str | None) -> str | None:
    """Event timestamps are naive local ISO strings; HA timestamp sensors accept them."""
    return event_ts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd teslausb-viewer && PYTHONPATH=. python3 tests/test_video_thumb.py`
Expected: `RESULT: ALL PASS`, exit 0.

- [ ] **Step 5: Commit**

```bash
cd teslausb-viewer
git add app/stats.py tests/test_video_thumb.py
git commit -m "feat(teslausb-viewer): stats from local disk usage, not rclone about"
```

---

### Task 5: Delete the now-dead rclone/stream files

**Files:**
- Delete: `teslausb-viewer/app/rclone.py`
- Delete: `teslausb-viewer/app/stream.py`
- Delete: `teslausb-viewer/tests/test_stream.py`
- Delete: `teslausb-viewer/tests/fake_rclone.py`
- Delete: `teslausb-viewer/scripts/rclone-check.sh`

**Interfaces:**
- Consumes: nothing (this task only removes files no other module imports as of Task 3
  Step 7 and Task 4).
- Produces: nothing new — later tasks must not reintroduce any `rclone` reference.

- [ ] **Step 1: Verify nothing still imports the modules being deleted**

Run:
```bash
cd teslausb-viewer
grep -rn "rclone\|StreamServer" app/ --include='*.py'
```
Expected: no output (Tasks 3 and 4 already removed every reference in `app/`).

- [ ] **Step 2: Delete the files**

```bash
cd teslausb-viewer
git rm app/rclone.py app/stream.py tests/test_stream.py tests/fake_rclone.py scripts/rclone-check.sh
```

- [ ] **Step 3: Verify the app still imports cleanly**

Run:
```bash
cd teslausb-viewer
PYTHONPATH=. python3 -c "import app.main"
```
Expected: no output, exit 0 (no `ImportError`).

- [ ] **Step 4: Commit**

```bash
cd teslausb-viewer
git commit -m "chore(teslausb-viewer): delete dead rclone/stream code and its tests"
```

---

### Task 6: HA long-lived token auth dependency (`app/auth.py`)

**Files:**
- Create: `teslausb-viewer/app/auth.py`
- Test: `teslausb-viewer/tests/test_auth.py` (new)

**Interfaces:**
- Consumes: nothing new (uses `httpx`, already a dependency).
- Produces: `require_ha_token(request: Request) -> None` — a FastAPI dependency, importable
  as `from .auth import require_ha_token`. Task 7's upload route depends on exactly this name
  and signature.

- [ ] **Step 1: Write the failing test**

```python
# teslausb-viewer/tests/test_auth.py
"""Unit checks for require_ha_token (mocks the Supervisor core API call)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeAsyncClient:
    def __init__(self, *, status_code, raise_transport_error=False):
        self._status = status_code
        self._raise = raise_transport_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, *_a, **_kw):
        import httpx
        if self._raise:
            raise httpx.TransportError("boom")
        return _FakeResponse(self._status)


def run():
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    import app.auth as auth_mod

    app = FastAPI()

    @app.get("/protected")
    async def protected(_: None = Depends(auth_mod.require_ha_token)):
        return {"ok": True}

    failures = []

    def check(name, cond, extra=""):
        print(("PASS" if cond else "FAIL"), name, extra)
        if not cond:
            failures.append(name)

    with TestClient(app) as c:
        r = c.get("/protected")
        check("no header -> 401", r.status_code == 401, str(r.status_code))

        r = c.get("/protected", headers={"Authorization": "Bearer "})
        check("empty token -> 401", r.status_code == 401, str(r.status_code))

        auth_mod.httpx.AsyncClient = lambda **kw: _FakeAsyncClient(status_code=200)
        r = c.get("/protected", headers={"Authorization": "Bearer good-token"})
        check("valid token -> 200", r.status_code == 200, str(r.status_code))

        auth_mod.httpx.AsyncClient = lambda **kw: _FakeAsyncClient(status_code=401)
        r = c.get("/protected", headers={"Authorization": "Bearer bad-token"})
        check("rejected token -> 401", r.status_code == 401, str(r.status_code))

        auth_mod.httpx.AsyncClient = lambda **kw: _FakeAsyncClient(
            status_code=200, raise_transport_error=True
        )
        r = c.get("/protected", headers={"Authorization": "Bearer whatever"})
        check("supervisor unreachable -> 401", r.status_code == 401, str(r.status_code))

    print()
    print("RESULT:", "ALL PASS" if not failures else f"{len(failures)} FAILURE(S): {failures}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd teslausb-viewer && PYTHONPATH=. python3 tests/test_auth.py`
Expected: `ModuleNotFoundError: No module named 'app.auth'`.

- [ ] **Step 3: Create `app/auth.py`**

```python
# teslausb-viewer/app/auth.py
"""FastAPI dependency validating a caller-supplied Home Assistant long-lived access token.

The upload endpoint is reachable from the LAN (not just ingress), so it needs its own auth
independent of Home Assistant's ingress session. We don't mint or store tokens ourselves —
we validate the caller's bearer token by asking Home Assistant Core whether it recognises it,
via the Supervisor's core API proxy (this add-on has `homeassistant_api: true`, so Supervisor
routes http://supervisor/core/... through to HA Core using the caller's own token).
"""

from __future__ import annotations

import logging

import httpx
from fastapi import HTTPException, Request

log = logging.getLogger("teslausb_viewer.auth")

_SUPERVISOR_CORE_API = "http://supervisor/core/api/"


async def require_ha_token(request: Request) -> None:
    """Raise 401 unless Authorization: Bearer <token> is a token HA Core accepts."""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    token = auth[len("bearer "):].strip()
    if not token:
        raise HTTPException(401, "missing bearer token")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                _SUPERVISOR_CORE_API, headers={"Authorization": f"Bearer {token}"}
            )
    except httpx.TransportError:
        log.warning("Could not reach Supervisor to validate token")
        raise HTTPException(401, "token validation unavailable")
    if resp.status_code != 200:
        raise HTTPException(401, "invalid token")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd teslausb-viewer && PYTHONPATH=. python3 tests/test_auth.py`
Expected: `RESULT: ALL PASS`, exit 0.

- [ ] **Step 5: Commit**

```bash
cd teslausb-viewer
git add app/auth.py tests/test_auth.py
git commit -m "feat(teslausb-viewer): validate uploads against a HA long-lived access token"
```

---

### Task 7: Upload endpoint (`app/upload.py`)

**Files:**
- Create: `teslausb-viewer/app/upload.py`
- Modify: `teslausb-viewer/app/main.py`
- Test: `teslausb-viewer/tests/test_upload.py` (new)

**Interfaces:**
- Consumes: `require_ha_token` from `app/auth.py` (Task 6); `FOLDERS`, `EVENT_DIR_RE`,
  `CLIP_RE` from `app/models.py` (unchanged); `request.app.state.settings` (Task 1's
  `Settings`).
- Produces: `router: APIRouter` (mounted in `main.py` as `upload_router`) exposing
  `PUT /api/upload/{folder}/{event_dir}/{filename}`; `sweep_orphaned_tmp(teslacam_path: Path,
  *, max_age_seconds: float) -> int` — Task 8 wires this into the scan loop.

- [ ] **Step 1: Write the failing test**

```python
# teslausb-viewer/tests/test_upload.py
"""End-to-end checks for the upload endpoint. Write-behaviour checks bypass real HA-token
validation via a FastAPI dependency override; the last check exercises the real (mocked)
auth path."""
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def run():
    work = tempfile.mkdtemp()
    try:
        teslacam = os.path.join(work, "teslacam")
        os.makedirs(teslacam)

        os.environ["TUV_TESLACAM_PATH"] = teslacam
        os.environ["TUV_DATA_DIR"] = work
        os.environ["TUV_CACHE_DIR"] = os.path.join(work, "cache")
        os.environ["TUV_MQTT_ENABLED"] = "false"
        os.environ["TUV_REFRESH_MINUTES"] = "5"

        from app.config import get_settings
        get_settings.cache_clear()

        from fastapi.testclient import TestClient
        import app.main as m
        from app.auth import require_ha_token
        from app.upload import sweep_orphaned_tmp

        failures = []

        def check(name, cond, extra=""):
            print(("PASS" if cond else "FAIL"), name, extra)
            if not cond:
                failures.append(name)

        m.app.dependency_overrides[require_ha_token] = lambda: None

        with TestClient(m.app) as c:
            r = c.put(
                "/api/upload/SavedClips/2024-01-15_10-30-22/2024-01-15_10-30-22-front.mp4",
                content=b"hello-clip-bytes",
            )
            check("valid upload -> 204", r.status_code == 204, str(r.status_code))
            written = os.path.join(
                teslacam, "SavedClips", "2024-01-15_10-30-22", "2024-01-15_10-30-22-front.mp4"
            )
            check("file written to disk", os.path.isfile(written))
            check("file contents match", open(written, "rb").read() == b"hello-clip-bytes")

            r = c.put(
                "/api/upload/SavedClips/2024-01-15_10-30-22/event.json",
                content=b'{"reason":"user_interaction_honk"}',
            )
            check("sidecar upload -> 204", r.status_code == 204, str(r.status_code))

            r = c.put("/api/upload/NotAFolder/2024-01-15_10-30-22/thumb.png", content=b"x")
            check("bad folder -> 400", r.status_code == 400, str(r.status_code))

            r = c.put("/api/upload/SavedClips/not-a-date/thumb.png", content=b"x")
            check("bad event_dir -> 400", r.status_code == 400, str(r.status_code))

            r = c.put("/api/upload/SavedClips/2024-01-15_10-30-22/not-a-clip.txt", content=b"x")
            check("bad filename -> 400", r.status_code == 400, str(r.status_code))

            r = c.put(
                "/api/upload/SavedClips/2024-01-15_10-30-22/2024-01-15_10-30-22-front.mp4",
                content=b"replaced",
            )
            check("re-upload -> 204", r.status_code == 204, str(r.status_code))
            check("re-upload overwrote contents", open(written, "rb").read() == b"replaced")
            leftovers = [n for n in os.listdir(os.path.dirname(written)) if ".tmp-" in n]
            check("no leftover tmp files", leftovers == [], str(leftovers))

            stale = os.path.join(os.path.dirname(written), ".stale.tmp-deadbeef")
            open(stale, "wb").close()
            old_time = time.time() - 3600
            os.utime(stale, (old_time, old_time))
            removed = sweep_orphaned_tmp(get_settings().teslacam_path, max_age_seconds=1)
            check("stale tmp swept", removed >= 1 and not os.path.exists(stale), str(removed))

        del m.app.dependency_overrides[require_ha_token]

        # Real auth path (no override): Supervisor isn't reachable in this test env, so the
        # dependency's TransportError branch fires and correctly denies the request.
        with TestClient(m.app) as c:
            r = c.put(
                "/api/upload/SavedClips/2024-01-15_10-30-22/2024-01-15_10-30-22-back.mp4",
                content=b"nope",
            )
            check("unauthenticated (unreachable supervisor) -> 401", r.status_code == 401,
                  str(r.status_code))

        print()
        print("RESULT:", "ALL PASS" if not failures else f"{len(failures)} FAILURE(S): {failures}")
        if failures:
            raise SystemExit(1)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    run()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd teslausb-viewer && PYTHONPATH=. python3 tests/test_upload.py`
Expected: `ModuleNotFoundError: No module named 'app.upload'`.

- [ ] **Step 3: Create `app/upload.py`**

```python
# teslausb-viewer/app/upload.py
"""Upload endpoint the Pi archiver calls to push TeslaCam clips onto local disk.

One PUT per file, in the exact TeslaUSB folder layout the indexer already expects
(models.FOLDERS / EVENT_DIR_RE / CLIP_RE). Writes are atomic (temp file + rename) so the
indexer never sees a half-written file. Upload order across an event's files does not
matter — db.incomplete_event_ids() already tolerates a folder that fills in over several
scan passes.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .auth import require_ha_token
from .models import CLIP_RE, EVENT_DIR_RE, FOLDERS

log = logging.getLogger("teslausb_viewer.upload")

router = APIRouter()

_SIDECAR_NAMES = {"event.json", "thumb.png"}


def _validate(folder: str, event_dir: str, filename: str) -> None:
    if folder not in FOLDERS:
        raise HTTPException(400, f"unknown folder {folder!r}")
    if not EVENT_DIR_RE.match(event_dir):
        raise HTTPException(400, f"invalid event directory {event_dir!r}")
    if filename not in _SIDECAR_NAMES and not CLIP_RE.match(filename):
        raise HTTPException(400, f"invalid filename {filename!r}")


@router.put(
    "/api/upload/{folder}/{event_dir}/{filename}",
    dependencies=[Depends(require_ha_token)],
)
async def upload(folder: str, event_dir: str, filename: str, request: Request) -> Response:
    _validate(folder, event_dir, filename)
    settings = request.app.state.settings
    if not settings.has_backend():
        raise HTTPException(503, "teslacam_path not available")

    body = await request.body()
    dest_dir = settings.teslacam_path / folder / event_dir
    dest = dest_dir / filename
    tmp = dest_dir / f".{filename}.tmp-{uuid.uuid4().hex}"

    def _write() -> None:
        dest_dir.mkdir(parents=True, exist_ok=True)
        with open(tmp, "wb") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, dest)

    try:
        await asyncio.to_thread(_write)
    except OSError as exc:
        log.warning("Upload write failed for %s/%s/%s: %s", folder, event_dir, filename, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(503, "could not write file") from exc
    return Response(status_code=204)


def sweep_orphaned_tmp(teslacam_path: Path, *, max_age_seconds: float) -> int:
    """Delete `.tmp-*` files older than max_age_seconds — remnants of an upload that never
    completed (e.g. a container restart mid-write). Returns the count removed. Synchronous;
    call via asyncio.to_thread from async code."""
    now = time.time()
    removed = 0
    for folder in FOLDERS:
        base = teslacam_path / folder
        if not base.is_dir():
            continue
        for root, _dirs, files in os.walk(base):
            for name in files:
                if ".tmp-" not in name:
                    continue
                path = os.path.join(root, name)
                try:
                    if now - os.path.getmtime(path) > max_age_seconds:
                        os.unlink(path)
                        removed += 1
                except OSError:
                    pass
    return removed
```

- [ ] **Step 4: Wire the router into `app/main.py`**

Add to the imports:
```python
from .upload import router as upload_router
```

After the existing `app.include_router(router)` line, add:
```python
app.include_router(upload_router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd teslausb-viewer && PYTHONPATH=. python3 tests/test_upload.py`
Expected: `RESULT: ALL PASS`, exit 0.

- [ ] **Step 6: Commit**

```bash
cd teslausb-viewer
git add app/upload.py app/main.py tests/test_upload.py
git commit -m "feat(teslausb-viewer): add token-authenticated PUT upload endpoint"
```

---

### Task 8: Wire the orphaned-upload sweep into the scan loop

**Files:**
- Modify: `teslausb-viewer/app/main.py`
- Modify: `teslausb-viewer/tests/test_upload.py` (extend)

**Interfaces:**
- Consumes: `sweep_orphaned_tmp` from `app/upload.py` (Task 7); `Settings.refresh_minutes`
  (Task 1) as the sweep's `max_age_seconds` threshold (one scan interval).
- Produces: nothing new — this task only makes `refresh_and_publish` call the sweep.

- [ ] **Step 1: Extend the test**

In `teslausb-viewer/tests/test_upload.py`, inside the first `with TestClient(m.app) as c:`
block, after the "stale tmp swept" check (and before leaving that `with` block), add:

```python
            # Integration: /api/refresh itself sweeps stale orphaned tmp files (not just a
            # direct sweep_orphaned_tmp() call, exercised above).
            stale2 = os.path.join(os.path.dirname(written), ".stale2.tmp-cafef00d")
            open(stale2, "wb").close()
            old_time2 = time.time() - 3600
            os.utime(stale2, (old_time2, old_time2))
            r = c.post("/api/refresh")
            check("refresh via API -> 200", r.status_code == 200, str(r.status_code))
            check("refresh swept stale tmp file", not os.path.exists(stale2))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd teslausb-viewer && PYTHONPATH=. python3 tests/test_upload.py`
Expected: `FAIL refresh swept stale tmp file` (the file is still present — `main.py` doesn't
call the sweep yet).

- [ ] **Step 3: Wire the sweep into `refresh_and_publish` in `app/main.py`**

Add to the imports:
```python
from .upload import sweep_orphaned_tmp
```

In `refresh_and_publish`, after the existing thumbnail-backfill `try`/`except` block and
before the stats/MQTT `try`/`except` block, add:

```python
    try:
        removed = await asyncio.to_thread(
            sweep_orphaned_tmp, app.state.settings.teslacam_path,
            max_age_seconds=app.state.settings.refresh_minutes * 60,
        )
        if removed:
            log.info("Swept %d orphaned upload temp file(s)", removed)
    except Exception:  # noqa: BLE001 — sweep must never break a scan
        log.exception("Orphaned upload sweep failed")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd teslausb-viewer && PYTHONPATH=. python3 tests/test_upload.py`
Expected: `RESULT: ALL PASS`, exit 0.

- [ ] **Step 5: Commit**

```bash
cd teslausb-viewer
git add app/main.py tests/test_upload.py
git commit -m "feat(teslausb-viewer): sweep orphaned upload temp files on every scan"
```

---

### Task 9: Add-on packaging (`config.yaml`, `run.sh`, `Dockerfile`)

**Files:**
- Modify: `teslausb-viewer/config.yaml`
- Modify: `teslausb-viewer/run.sh`
- Modify: `teslausb-viewer/Dockerfile`

**Interfaces:**
- Consumes: `TUV_TESLACAM_PATH`/`TUV_REFRESH_MINUTES`/`TUV_CACHE_SIZE_MB`/`TUV_PORT`/
  `TUV_MQTT_*` env vars, all already read by `app/config.py` (Task 1).
- Produces: the running container's environment and exposed ports that every earlier task's
  code assumes.

- [ ] **Step 1: Rewrite `config.yaml`**

```yaml
name: "TeslaUSB Viewer"
description: "Browse and watch Tesla dashcam/Sentry videos archived locally by your car-lights Pi, with stats in Home Assistant"
version: "0.4.0"
slug: "teslausb_viewer"
init: false

# Supported architectures (v1 targets amd64; transcoding fallback is amd64-feasible)
arch:
  - amd64

# External resources
url: "https://github.com/marcone/teslausb"

# Web interface configuration — ingress for the UI, plus a LAN port for the upload API
ingress: true
ingress_port: 8099
ingress_stream: true   # stream responses through the proxy (video Range/seek passthrough)
panel_icon: mdi:car-connected
panel_title: "TeslaUSB Viewer"

# Service startup configuration
startup: services

# Volume mappings — index.db/thumbnail cache live under /data; teslacam_path lives under
# the host's /media (e.g. /media/USBDisk/teslausb).
map:
  - data:rw
  - media:rw

# Supervisor API access (bashio::info.timezone) + Home Assistant Core API access (needed to
# validate a caller's long-lived access token for the upload endpoint).
hassio_api: true
homeassistant_api: true

# Expose the ingress port on the LAN too, so an external device (not a browser session) can
# reach the upload API. The browse/watch UI is unaffected — it still works the same way
# through ingress.
ports:
  "8099/tcp": 8099
ports_description:
  "8099/tcp": "TeslaCam viewer UI (ingress) + upload API (LAN, token-authenticated)"

# Auto-discover an MQTT broker for statistics entities, but still run without one
services:
  - mqtt:want

# Add-on configuration options
options:
  teslacam_path: "/media/USBDisk/teslausb"
  refresh_interval_minutes: 30
  cache_size_mb: 2048
  publish_mqtt: true
schema:
  teslacam_path: str
  refresh_interval_minutes: int(5,1440)
  cache_size_mb: int(256,51200)
  publish_mqtt: bool?
```

- [ ] **Step 2: Rewrite `run.sh`**

```bash
#!/usr/bin/with-contenv bashio
# NB: deliberately no `set -e`/`set -o pipefail`. bashio helpers run internal pipelines
# whose benign non-zero stages would abort the whole startup under those options. Each
# step below handles its own errors; the app degrades gracefully if teslacam_path is absent.

APP_DIR="/opt/teslausb-viewer"
DATA_DIR="/data"
CACHE_DIR="${DATA_DIR}/cache"
PORT=8099

bashio::log.info "Starting TeslaUSB Viewer add-on..."

# --- Initialise persistent storage -----------------------------------------
mkdir -p "${CACHE_DIR}"

# --- Resolve and prepare the local TeslaCam directory -----------------------
TESLACAM_PATH="$(bashio::config 'teslacam_path')"
if [ -z "${TESLACAM_PATH}" ] || [ "${TESLACAM_PATH}" = "null" ]; then
    TESLACAM_PATH="/media/USBDisk/teslausb"
fi
mkdir -p "${TESLACAM_PATH}" || bashio::log.warning "Could not create ${TESLACAM_PATH}"

# This add-on is the sole owner/writer of teslacam_path (the Pi archiver writes over the
# network via the upload API, not directly to the filesystem), so a recursive chown at
# startup is safe — same pattern as /data below.
chown -R viewer:viewer "${DATA_DIR}" || bashio::log.warning "Could not chown ${DATA_DIR}"
chown -R viewer:viewer "${TESLACAM_PATH}" || bashio::log.warning "Could not chown ${TESLACAM_PATH}"

# --- Application configuration via environment ------------------------------
export TUV_DATA_DIR="${DATA_DIR}"
export TUV_TESLACAM_PATH="${TESLACAM_PATH}"
export TUV_CACHE_DIR="${CACHE_DIR}"
export TUV_REFRESH_MINUTES="$(bashio::config 'refresh_interval_minutes')"
export TUV_CACHE_SIZE_MB="$(bashio::config 'cache_size_mb')"
export TUV_PORT="${PORT}"

# Match "today" calculations to Home Assistant's configured timezone.
TZ_VALUE="$(bashio::info.timezone 2>/dev/null)"
if [ -n "${TZ_VALUE}" ] && [ "${TZ_VALUE}" != "null" ]; then
    export TZ="${TZ_VALUE}"
fi

if [ -d "${TESLACAM_PATH}" ]; then
    bashio::log.info "TeslaCam directory ready: ${TESLACAM_PATH}"
else
    bashio::log.warning "teslacam_path (${TESLACAM_PATH}) is not a directory — the UI will still load"
fi

# --- MQTT (optional) --------------------------------------------------------
if bashio::config.true 'publish_mqtt' && bashio::services.available 'mqtt'; then
    export TUV_MQTT_ENABLED="true"
    export TUV_MQTT_HOST="$(bashio::services 'mqtt' 'host')"
    export TUV_MQTT_PORT="$(bashio::services 'mqtt' 'port')"
    export TUV_MQTT_USERNAME="$(bashio::services 'mqtt' 'username')"
    export TUV_MQTT_PASSWORD="$(bashio::services 'mqtt' 'password')"
    bashio::log.info "MQTT broker discovered — statistics entities will be published"
else
    export TUV_MQTT_ENABLED="false"
    bashio::log.info "MQTT disabled or no broker available — skipping statistics entities"
fi

# --- Launch the web app as the unprivileged user ----------------------------
bashio::log.info "Launching web app on port ${PORT} (ingress + LAN upload API)"
cd "${APP_DIR}"
exec gosu viewer "${APP_DIR}/venv/bin/uvicorn" app.main:app \
    --host 0.0.0.0 --port "${PORT}" --workers 1 --no-access-log
```

- [ ] **Step 3: Rewrite `Dockerfile`**

```dockerfile
ARG BUILD_FROM
FROM ${BUILD_FROM}

# System dependencies:
#   python3 venv  -> the FastAPI app
#   ffmpeg        -> poster-frame thumbnail generation (thumbnailer.py) and the reserved
#                    future HEVC->H.264 transcoding fallback
#   gosu          -> drop root to the unprivileged app user at startup
#   curl/ca-certs -> required by bashio at runtime (bashio talks to the Supervisor API over curl)
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        ffmpeg \
        gosu \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged user that owns /data and the bind-mounted TeslaCam directory, and runs the web app
RUN groupadd --gid 1000 viewer \
    && useradd --uid 1000 --gid viewer --shell /bin/bash --create-home viewer

# Python dependencies managed with uv (https://github.com/astral-sh/uv)
COPY --from=ghcr.io/astral-sh/uv:0.5.13 /uv /usr/local/bin/uv
RUN uv venv /opt/teslausb-viewer/venv
COPY requirements.txt /opt/teslausb-viewer/requirements.txt
RUN VIRTUAL_ENV=/opt/teslausb-viewer/venv \
    uv pip install --no-cache -r /opt/teslausb-viewer/requirements.txt

# Application code
COPY app/ /opt/teslausb-viewer/app/

COPY run.sh /run.sh
RUN chmod +x /run.sh

WORKDIR /opt/teslausb-viewer
CMD ["/run.sh"]
```

- [ ] **Step 4: Verify syntax**

Run:
```bash
cd teslausb-viewer
bash -n run.sh && echo "run.sh OK"
python3 -c "import yaml; yaml.safe_load(open('config.yaml'))" 2>/dev/null \
  || python3 -c "import json,subprocess; print('config.yaml present, yaml module unavailable — skip parse')"
```
Expected: `run.sh OK` (the `yaml` check is best-effort — if PyYAML isn't installed locally,
that's fine, the Supervisor validates `config.yaml` at add-on install time).

- [ ] **Step 5: Commit**

```bash
cd teslausb-viewer
git add config.yaml run.sh Dockerfile
git commit -m "feat(teslausb-viewer): package teslacam_path + LAN upload port, drop rclone install"
```

---

### Task 10: Test harness rewrite, docs, and version bump

**Files:**
- Rewrite: `teslausb-viewer/tests/run.sh`
- Rewrite: `teslausb-viewer/tests/test_api.py`
- Modify: `teslausb-viewer/DOCS.md`
- Modify: `teslausb-viewer/README.md`
- Modify: `teslausb-viewer/build.yaml`
- Modify: `teslausb-viewer/CHANGELOG.md`
- Modify: `teslausb-viewer/app/__init__.py`

**Interfaces:**
- Consumes: every module from Tasks 1–9.
- Produces: nothing new — this is the final integration pass tying everything together and
  documenting it.

- [ ] **Step 1: Rewrite `tests/run.sh`**

```bash
#!/usr/bin/env bash
# Set up an isolated test environment (uv venv + local TeslaCam sample tree) and run the
# full test suite. Requires `uv` on PATH.
set -euo pipefail

ADDON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

VENV="$WORK/venv"
DATA="$WORK/data"
TESLACAM="$WORK/teslacam"
BIN="$WORK/bin"

# uv-managed virtual environment with the app deps + test client.
uv venv "$VENV" >/dev/null
VIRTUAL_ENV="$VENV" uv pip install --quiet -r "$ADDON_DIR/requirements.txt" httpx >/dev/null

# Fake ffmpeg first on PATH (real frame extraction needs a decodable video; the sample tree
# uses random bytes).
mkdir -p "$BIN"
cp "$ADDON_DIR/tests/fake_ffmpeg.py" "$BIN/ffmpeg"
chmod +x "$BIN/ffmpeg"

# Sample SavedClips event (front + back cameras, one minute).
EV="$TESLACAM/SavedClips/2024-01-15_10-30-22"
mkdir -p "$EV" "$DATA/cache"
printf '{"reason":"user_interaction_honk","city":"Seattle","est_lat":47.6,"est_lon":-122.3}' > "$EV/event.json"
printf 'PNGDATA' > "$EV/thumb.png"
head -c 4096 /dev/urandom > "$EV/2024-01-15_10-30-22-front.mp4"
head -c 4096 /dev/urandom > "$EV/2024-01-15_10-30-22-back.mp4"

# Sample RecentClips clips nested under a date sub-folder (non-flat layout) to prove the
# indexer descends into it and playback fetches by the clip's real path.
REC="$TESLACAM/RecentClips/2024-01-15"
mkdir -p "$REC"
head -c 2048 /dev/urandom > "$REC/2024-01-15_10-31-00-front.mp4"
head -c 2048 /dev/urandom > "$REC/2024-01-15_10-31-00-back.mp4"

# test_config/test_indexer/test_video_thumb/test_auth/test_upload each build their own
# isolated temp fixture internally; test_api.py uses this shared sample tree, mirroring how
# run.sh configures the real container.
for t in test_config test_indexer test_video_thumb test_auth test_upload test_api; do
  echo "=== ${t} ==="
  PATH="$BIN:$PATH" PYTHONPATH="$ADDON_DIR" \
    TUV_TESLACAM_PATH="$TESLACAM" TUV_DATA_DIR="$DATA" TUV_CACHE_DIR="$DATA/cache" \
    TUV_MQTT_ENABLED=false \
    "$VENV/bin/python" "$ADDON_DIR/tests/${t}.py"
done
```

- [ ] **Step 2: Rewrite `tests/test_api.py`**

```python
"""End-to-end UI/API checks not already covered by test_indexer.py, test_video_thumb.py,
test_auth.py, or test_upload.py: event listing/filtering, ingress base-path injection (incl.
the XSS guard), static asset content, version injection, and the MQTT publisher smoke test.

Run with:  ./tests/run.sh
"""
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as m

EVENT_ID = "SavedClips/2024-01-15_10-30-22"
failures = []


def check(name, cond, extra=""):
    print(("PASS" if cond else "FAIL"), name, extra)
    if not cond:
        failures.append(name)


def run():
    with TestClient(m.app) as c:
        r = c.post("/api/refresh")
        check("refresh 200", r.status_code == 200, str(r.json()))
        check("refresh added>=1", r.json().get("added", 0) >= 1)

        h = c.get("/api/health").json()
        check("health backend_configured", h["backend_configured"] is True, str(h))

        data = c.get("/api/events?folder=SavedClips").json()
        check("events listed", data["total"] >= 1, str(data["total"]))
        ev = data["events"][0]
        check("reason parsed", ev["reason"] == "user_interaction_honk", str(ev["reason"]))
        check("city parsed", ev["city"] == "Seattle", str(ev["city"]))
        check("file_count==2", ev["file_count"] == 2, str(ev["file_count"]))
        check("thumb_present", ev["thumb_present"] is True)

        d = c.get(f"/api/events/{EVENT_ID}/detail").json()
        check("cameras ordered", d["cameras"] == ["front", "back"], str(d["cameras"]))
        check("one minute", len(d["minutes"]) == 1, str(len(d["minutes"])))
        check("detail exposes coordinates",
              d.get("est_lat") == 47.6 and d.get("est_lon") == -122.3,
              f"{d.get('est_lat')},{d.get('est_lon')}")

        r = c.get("/api/events?folder=SavedClips&date_from=2024-01-15T00:00:00&date_to=2024-01-15T23:59:59")
        check("date filter matches day", r.json()["total"] == 1, str(r.json()["total"]))
        r = c.get("/api/events?folder=SavedClips&date_from=2024-02-01T00:00:00&date_to=2024-02-01T23:59:59")
        check("date filter excludes other day", r.json()["total"] == 0, str(r.json()["total"]))

        rec = c.get("/api/events?folder=RecentClips").json()
        check("recent listed", rec["total"] >= 1, str(rec["total"]))
        check("recent thumb_present false", rec["events"][0]["thumb_present"] is False,
              str(rec["events"][0]["thumb_present"]))

        s = c.get("/api/stats").json()
        check("stats savedclips_count", s["savedclips_count"] >= 1, str(s.get("savedclips_count")))
        check("stats backend_total_bytes present", isinstance(s.get("backend_total_bytes"), int),
              str(s.get("backend_total_bytes")))

        r = c.get("/", headers={"X-Ingress-Path": "/api/hassio_ingress/TOKEN"})
        check("ingress base injected", 'window.INGRESS_BASE = "/api/hassio_ingress/TOKEN"' in r.text)
        check("assets use base", "/api/hassio_ingress/TOKEN/static/app.js" in r.text)

        import app as _app
        check("version injected into shell", f"v{_app.__version__}" in r.text, _app.__version__)
        check("no unrendered version placeholder", "{{VERSION}}" not in r.text)
        cfg = (Path(__file__).resolve().parent.parent / "config.yaml").read_text()
        cfg_ver = next(l.split('"')[1] for l in cfg.splitlines() if l.startswith("version:"))
        check("__version__ matches config.yaml", _app.__version__ == cfg_ver,
              f"{_app.__version__} vs {cfg_ver}")

        pjs = c.get("/static/player.js").text
        check("player mutes all tiles", "v.muted = true" in pjs)
        check("no unmuted master (autoplay gate)", "cam !== master" not in pjs)
        check("metadata overlay present", "meta-overlay" in pjs and "meta-clock" in pjs)
        check("overlay toggle present", "meta-toggle" in pjs)
        bjs = c.get("/static/browser.js").text
        check("reasonLabel exposed for overlay", "TUV.reasonLabel = reasonLabel" in bjs)

        idx = c.get("/", headers={"X-Ingress-Path": "/api/hassio_ingress/TOKEN"}).text
        check("default tab is All", 'data-folder="" class="active"' in idx)

        r = c.get("/", headers={"X-Ingress-Path": '/x"></script><script>alert(1)</script>'})
        check("malicious ingress header neutralised",
              "alert(1)" not in r.text and 'window.INGRESS_BASE = ""' in r.text)

    from dataclasses import replace
    from app.config import get_settings
    from app.mqtt_publisher import MqttPublisher

    s = replace(get_settings(), mqtt_enabled=True, mqtt_host="127.0.0.1", mqtt_port=1)
    pub = MqttPublisher(s)
    try:
        pub.start()                      # dead broker — must not raise
        pub.publish_states({"total_events": 1})  # not connected — must be a no-op
        check("mqtt start survives dead broker", pub.connected is False)
    except Exception as e:               # noqa: BLE001
        check("mqtt start survives dead broker", False, repr(e))
    finally:
        pub.stop()

    print()
    print("RESULT:", "ALL PASS" if not failures else f"{len(failures)} FAILURE(S): {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())
```

- [ ] **Step 3: Run the full suite**

Run: `cd teslausb-viewer && ./tests/run.sh`
Expected: every `=== test_* ===` section prints `RESULT: ALL PASS` (or, for `test_config.py`/
`test_indexer.py`, their single `PASS ...` line with no `AssertionError`), overall exit 0.

- [ ] **Step 4: Bump the version**

In `teslausb-viewer/app/__init__.py`, change:
```python
__version__ = "0.3.0"
```
to:
```python
__version__ = "0.4.0"
```
(`config.yaml` was already set to `"0.4.0"` in Task 9 — `test_api.py`'s
`__version__ matches config.yaml` check enforces they stay in sync.)

- [ ] **Step 5: Rewrite `DOCS.md`**

```markdown
# TeslaUSB Viewer

Browse and watch the Tesla dashcam / Sentry videos your car-lights Pi archives to this Home
Assistant host — directly inside Home Assistant, behind ingress, with statistics exported as
sensors.

## How it works

The Pi-based archiver (a ROCK Pi 4C+ running the car-lights TeslaUSB gadget) pushes clips
straight to this add-on over the LAN, one file at a time, via an authenticated upload API —
no cloud backend, no rclone. Uploaded files land on this Home Assistant host's own disk (at
`teslacam_path`, e.g. a mounted USB drive) in the same `SavedClips/SentryClips/RecentClips`
layout Tesla itself uses. This add-on just indexes and serves what's on disk.

> **Ingress + LAN.** The browse/watch UI is reachable through the Home Assistant sidebar
> panel (ingress). The upload API is also reachable on the LAN (port 8099), authenticated by
> a Home Assistant long-lived access token — see "Archiver setup" below.

## Configuration

| Option | Description |
| --- | --- |
| `teslacam_path` | Path **inside the container** where TeslaCam clips live (and where the upload API writes them) — the folder that contains (or will contain) `SavedClips/`, `SentryClips/`, `RecentClips/`. Defaults to `/media/USBDisk/teslausb`; requires `/media` to be reachable on the host (this add-on's `map` includes `media:rw`). |
| `refresh_interval_minutes` | How often to re-scan `teslacam_path` for new events (5–1440). Also bounds how long an orphaned upload temp file can linger before being swept. |
| `cache_size_mb` | Maximum size of the on-disk thumbnail cache before older entries are reclaimed (256–51200). |
| `publish_mqtt` | Publish statistics to Home Assistant via MQTT discovery (needs the Mosquitto broker + MQTT integration). |

## Archiver setup (authenticating the Pi)

The upload API validates the caller against Home Assistant itself — issue the Pi a **long-
lived access token**:

1. In Home Assistant, open your user profile → **Security** → **Long-lived access tokens** →
   **Create token**. Copy it immediately (shown once).
2. Configure the Pi archiver with that token and this add-on's LAN address, e.g.
   `http://<home-assistant-host>:8099/api/upload/`.
3. The archiver `PUT`s each clip file to
   `.../api/upload/<SavedClips|SentryClips|RecentClips>/<event_dir>/<filename>` with
   `Authorization: Bearer <token>` and the raw file bytes as the body.

## Statistics entities

When an MQTT broker is available and `publish_mqtt` is on, a **TeslaUSB Viewer** device is
created with sensors: total events, Saved/Sentry/Recent counts, total video files, last
event (timestamp), Sentry events today, last index refresh (timestamp), and disk used/free
bytes for `teslacam_path`.

## Codec note (HEVC)

Tesla HW3+ vehicles record **H.265/HEVC**. Safari plays this natively; Chrome/Firefox play
it only when the operating system provides HEVC decoding, otherwise the affected camera
tile shows a "can't decode" message. Browsing, thumbnails and Saved/Sentry indexing work
everywhere — only playback depends on the codec. On-the-fly transcoding to H.264 is a
planned enhancement.

## Current scope

This first release indexes **SavedClips**, browses them, and plays synchronized
multi-camera footage one minute ("scene") at a time. SentryClips/RecentClips, transcoding,
and a guided first-run setup screen are on the roadmap (see `CHANGELOG.md`).

## Troubleshooting

- **No events / empty list** — click **↻ Refresh**, and check the add-on log. The log
  reports whether `teslacam_path` was present at startup.
- **"teslacam_path not available"** — verify the option points at a path under `/media`
  (this add-on only has access to `/media`, via its `map: media:rw`), and that the directory
  exists (the add-on creates it at startup if missing, but a typo'd path under a drive that
  isn't mounted will not appear).
- **Uploads return 401** — the long-lived access token is missing, expired, or was revoked;
  issue a new one (see "Archiver setup") and update the Pi's configuration.
- **Black video** — almost always the HEVC codec issue above; try Safari.
```

- [ ] **Step 6: Rewrite `README.md`**

```markdown
# TeslaUSB Viewer — Home Assistant Add-on

Browse and watch the Tesla dashcam & Sentry videos your car-lights Pi archives to this Home
Assistant host — from the Home Assistant sidebar, behind ingress.

## Features

- 📂 **Browse** Saved (and, on the roadmap, Sentry/Recent) events as a thumbnail grid, filter by date.
- 🎥 **Synchronized multi-camera playback** — up to six angles play together against a master clock, with play/pause/seek/speed.
- 📥 **Authenticated upload API** — the Pi archiver pushes clips directly to this host's own disk (no cloud backend, no rclone).
- 🔒 **Ingress for the UI, token-authenticated LAN API for uploads** — no anonymous write access.
- 📊 **Statistics entities** in Home Assistant via MQTT discovery (event counts, last event, disk usage, …).

## Quick start

1. Install the add-on and open its **Configuration** tab.
2. Set `teslacam_path` (default `/media/USBDisk/teslausb`) to where you want clips stored.
3. Start the add-on and open **TeslaUSB Viewer** from the sidebar.
4. Issue a Home Assistant long-lived access token and configure your Pi archiver to upload
   with it — see [`DOCS.md`](./DOCS.md)'s "Archiver setup" section.

See [`DOCS.md`](./DOCS.md) for full configuration, the HEVC codec note, and troubleshooting.
```

- [ ] **Step 7: Update `build.yaml` labels**

```yaml
build_from:
  amd64: ghcr.io/home-assistant/amd64-base-debian:bookworm

labels:
  org.opencontainers.image.title: "Home Assistant Add-on: TeslaUSB Viewer"
  org.opencontainers.image.description: "Browse and watch TeslaCam dashcam/Sentry videos archived locally by the car-lights Pi"
  org.opencontainers.image.source: "https://github.com/mattbsea/car-lights"
  org.opencontainers.image.licenses: "MIT"
```

- [ ] **Step 8: Prepend a `CHANGELOG.md` entry**

At the very top of `teslausb-viewer/CHANGELOG.md`, immediately after the `# Changelog`
heading and before the existing `## 0.3.0` section, insert:

```markdown
## 0.4.0

### 💥 Breaking changes
- **Dropped rclone/S3 entirely.** The add-on no longer reads from a remote backend
  (S3/MinIO/Drive/SMB/etc.) — it now reads and serves TeslaCam clips directly from a local
  bind-mounted directory (`teslacam_path`, default `/media/USBDisk/teslausb`). All rclone/S3
  config options (`rclone_conf`, `remote_name`, `remote_path`, `s3_*`) are removed.
- **New upload API.** `PUT /api/upload/{folder}/{event_dir}/{filename}` (bearer-token
  authenticated with a Home Assistant long-lived access token) lets an external archiver
  push clips directly onto this host's disk, in the same folder layout the indexer expects.
  See DOCS.md's "Archiver setup" section.
- **No longer ingress-only.** The add-on now also exposes its port on the LAN so the upload
  API is reachable from outside Home Assistant's ingress proxy (the browse/watch UI is
  unaffected and still works the same way through ingress).
```

- [ ] **Step 9: Run the full suite once more**

Run: `cd teslausb-viewer && ./tests/run.sh`
Expected: every section `RESULT: ALL PASS`, overall exit 0 (the version-match check in
`test_api.py` now passes against the bumped `0.4.0`).

- [ ] **Step 10: Commit**

```bash
cd teslausb-viewer
git add tests/run.sh tests/test_api.py DOCS.md README.md build.yaml CHANGELOG.md app/__init__.py
git commit -m "docs(teslausb-viewer): rewrite docs for local upload API, bump to 0.4.0"
```

---

## Self-Review

**Spec coverage:**
- §3.1 config.yaml changes → Task 9.
- §3.2 upload endpoint (validation, atomic write, 400/401/503, order-independence) → Task 7,
  Task 8 (sweep).
- §3.3 auth (HA token via Supervisor proxy) → Task 6.
- §3.4 local reads replace rclone (indexer/api/cache/thumbnailer/main) → Tasks 2, 3, 4, 5.
- §3.5 ownership (chown teslacam_path) → Task 9.
- §4 error handling (400 validation, 503 unavailable, orphaned `.tmp` sweep, auth failure
  modes) → Tasks 6, 7, 8.
- §5 testing (test_api.py rebase, test_upload.py, test_auth.py) → Tasks 6, 7, 10.
- §6 documentation (DOCS.md/README.md rewrite) → Task 10.

**Placeholder scan:** no `TBD`/`TODO`/"add appropriate" phrasing anywhere above; every step
has complete, exact code.

**Type consistency:** `Settings.teslacam_path: Path` (Task 1) is the name every later task
uses (`app/indexer.py`, `app/cache.py`, `app/thumbnailer.py`, `app/api.py`, `app/stats.py`,
`app/upload.py`) — no renaming drift. `require_ha_token` (Task 6) matches its `Depends(...)`
usage in Task 7. `sweep_orphaned_tmp(teslacam_path: Path, *, max_age_seconds: float) -> int`
(Task 7) matches its call in Task 8's `main.py` edit and its direct call in Task 7's own test.
`serve_file_range(path: Path, range_header: str | None) -> Response` (Task 3) matches its
usage in `api.py`'s `/video` handler.
