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
