"""Incremental scan of the backend into the SQLite index.

Cost control is the whole point: enumerating event folders (`lsjson --dirs-only`) is cheap,
but listing every file in every event on every refresh is not. Archived Saved/Sentry events
are append-only, so we list a folder's files only the first time we see it. RecentClips is a
rolling buffer, so it is re-listed and pruned each pass. Phase 1 scans SavedClips only;
SentryClips/RecentClips are flipped on in later phases via SCAN_FOLDERS.
"""

from __future__ import annotations

import asyncio
import json
import logging

from . import rclone
from .config import Settings
from .db import Database
from .models import (
    CLIP_RE,
    EVENT_DIR_RE,
    Event,
    event_id,
    parse_event_timestamp,
    utc_now_iso,
)

log = logging.getLogger("teslausb_viewer.indexer")

# Phase 1: SavedClips only. Later phases append "SentryClips", "RecentClips".
SCAN_FOLDERS = ("SavedClips",)
EVENT_FOLDERS = ("SavedClips", "SentryClips")


def _build_files(entries: list[dict]) -> list:
    from .models import CameraFile

    files = []
    for e in entries:
        m = CLIP_RE.match(e.get("Name", ""))
        if not m:
            continue
        files.append(
            CameraFile(
                camera=m.group("camera"),
                minute_ts=m.group("minute"),
                filename=e["Name"],
                size=int(e.get("Size", 0) or 0),
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
                log.info("No backend configured — skipping scan")
                return {"scanned": 0, "added": 0, "skipped": True}
            added = 0
            for folder in SCAN_FOLDERS:
                if folder in EVENT_FOLDERS:
                    added += await self._scan_event_folder(folder)
                else:
                    added += await self._scan_recent(folder)
            self.db.set_meta("last_index_refresh", utc_now_iso())
            log.info("Scan complete: %d new event(s) indexed", added)
            return {"added": added, "skipped": False}

    async def _scan_event_folder(self, folder: str) -> int:
        try:
            dirs = await rclone.lsjson(self.settings, folder, dirs_only=True)
        except rclone.RcloneError as exc:
            log.warning("Could not list %s: %s", folder, exc.stderr.strip()[:200])
            return 0
        known = self.db.known_event_ids(folder)
        incomplete = self.db.incomplete_event_ids(folder)
        now = utc_now_iso()
        added = 0
        for entry in dirs:
            name = entry.get("Name", "")
            if not EVENT_DIR_RE.match(name):
                continue
            eid = event_id(folder, name)
            # Skip events already fully indexed; re-list new or mid-upload (incomplete) ones.
            if eid in known and eid not in incomplete:
                continue
            await self._index_event(folder, name, eid, now)
            if eid not in known:
                added += 1
        return added

    async def _index_event(self, folder: str, name: str, eid: str, now: str) -> None:
        ts = parse_event_timestamp(name)
        subpath = f"{folder}/{name}"
        try:
            files_entries = await rclone.lsjson(self.settings, subpath, files_only=True)
        except rclone.RcloneError as exc:
            log.warning("Could not list event %s: %s", subpath, exc.stderr.strip()[:200])
            return
        names = {e.get("Name", "") for e in files_entries}
        event = Event(
            event_id=eid,
            folder=folder,
            event_ts=ts.isoformat() if ts else name,
            thumb_present="thumb.png" in names,
            files=_build_files(files_entries),
        )
        if "event.json" in names:
            try:
                event_meta = _parse_event_json(await rclone.cat(self.settings, f"{subpath}/event.json"))
                event.reason = event_meta["reason"]
                event.city = event_meta["city"]
                event.est_lat = event_meta["est_lat"]
                event.est_lon = event_meta["est_lon"]
            except rclone.RcloneError as exc:
                log.info("No readable event.json for %s: %s", subpath, exc.stderr.strip()[:120])
        self.db.upsert_event(event, first_seen=now, now=now)

    async def _scan_recent(self, folder: str) -> int:
        """RecentClips: flat rolling buffer of clips grouped into synthetic per-minute events."""
        try:
            entries = await rclone.lsjson(self.settings, folder, files_only=True)
        except rclone.RcloneError as exc:
            log.warning("Could not list %s: %s", folder, exc.stderr.strip()[:200])
            return 0
        files = _build_files(entries)
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
