"""SQLite index store (one file at /data/index.db, WAL mode).

Chosen over a JSON blob because the index grows to thousands of events: incremental
upserts avoid rewriting the whole file, and filtered/paginated queries stay cheap. A
single writer (the scan task) plus WAL lets API reads run concurrently. All methods are
synchronous; async callers wrap them in asyncio.to_thread.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .models import CameraFile, Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id      TEXT PRIMARY KEY,
    folder        TEXT NOT NULL,
    event_ts      TEXT NOT NULL,
    reason        TEXT,
    city          TEXT,
    est_lat       REAL,
    est_lon       REAL,
    thumb_present INTEGER NOT NULL DEFAULT 0,
    first_seen    TEXT NOT NULL,
    last_scanned  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_folder_ts ON events(folder, event_ts DESC);

CREATE TABLE IF NOT EXISTS files (
    event_id  TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    camera    TEXT NOT NULL,
    minute_ts TEXT NOT NULL,
    filename  TEXT NOT NULL,
    size      INTEGER NOT NULL DEFAULT 0,
    path      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (event_id, filename)
);
CREATE INDEX IF NOT EXISTS idx_files_event ON files(event_id);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after the original schema, for DBs created by older versions."""
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(files)")}
        if "path" not in cols:
            self._conn.execute("ALTER TABLE files ADD COLUMN path TEXT NOT NULL DEFAULT ''")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- writes (scan task) -------------------------------------------------
    def known_event_ids(self, folder: str) -> set[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_id FROM events WHERE folder=?", (folder,)
            ).fetchall()
        return {r["event_id"] for r in rows}

    def incomplete_event_ids(self, folder: str) -> set[str]:
        """Events indexed before their thumbnail appeared — likely caught mid-upload.

        TeslaUSB uploads aren't atomic, so an event scanned before thumb.png/event.json
        land would otherwise stay incomplete forever. We re-index these each pass until a
        thumbnail shows up (rare thumbless events get re-listed cheaply but harmlessly).
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_id FROM events WHERE folder=? AND thumb_present=0", (folder,)
            ).fetchall()
        return {r["event_id"] for r in rows}

    def upsert_event(self, event: Event, *, first_seen: str, now: str) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO events
                     (event_id, folder, event_ts, reason, city, est_lat, est_lon,
                      thumb_present, first_seen, last_scanned)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(event_id) DO UPDATE SET
                     reason=excluded.reason, city=excluded.city,
                     est_lat=excluded.est_lat, est_lon=excluded.est_lon,
                     thumb_present=excluded.thumb_present, last_scanned=excluded.last_scanned""",
                (
                    event.event_id, event.folder, event.event_ts, event.reason, event.city,
                    event.est_lat, event.est_lon, int(event.thumb_present), first_seen, now,
                ),
            )
            self._conn.execute("DELETE FROM files WHERE event_id=?", (event.event_id,))
            self._conn.executemany(
                "INSERT OR REPLACE INTO files (event_id, camera, minute_ts, filename, size, path)"
                " VALUES (?,?,?,?,?,?)",
                [(event.event_id, f.camera, f.minute_ts, f.filename, f.size, f.path) for f in event.files],
            )
            self._conn.commit()

    def delete_events_not_in(self, folder: str, keep_ids: set[str]) -> None:
        """Prune events that vanished from the backend (e.g. RecentClips rolled over)."""
        with self._lock:
            existing = {
                r["event_id"]
                for r in self._conn.execute(
                    "SELECT event_id FROM events WHERE folder=?", (folder,)
                ).fetchall()
            }
            stale = existing - keep_ids
            if stale:
                self._conn.executemany(
                    "DELETE FROM events WHERE event_id=?", [(i,) for i in stale]
                )
                self._conn.commit()

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._conn.commit()

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    # --- reads (API) --------------------------------------------------------
    def list_events(
        self, *, folder: str | None, date_from: str | None, date_to: str | None,
        limit: int, offset: int,
    ) -> tuple[list[dict], int]:
        where, params = [], []
        if folder:
            where.append("folder=?")
            params.append(folder)
        if date_from:
            where.append("event_ts>=?")
            params.append(date_from)
        if date_to:
            where.append("event_ts<=?")
            params.append(date_to)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) AS n FROM events{clause}", params
            ).fetchone()["n"]
            rows = self._conn.execute(
                f"""SELECT e.*, COUNT(f.filename) AS file_count,
                           COUNT(DISTINCT f.minute_ts) AS minute_count
                    FROM events e LEFT JOIN files f ON f.event_id = e.event_id
                    {clause}
                    GROUP BY e.event_id
                    ORDER BY e.event_ts DESC
                    LIMIT ? OFFSET ?""",
                [*params, limit, offset],
            ).fetchall()
        return [dict(r) for r in rows], total

    def get_event(self, event_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM events WHERE event_id=?", (event_id,)
            ).fetchone()
            if not row:
                return None
            files = self._conn.execute(
                "SELECT camera, minute_ts, filename, size, path FROM files WHERE event_id=?"
                " ORDER BY minute_ts, camera",
                (event_id,),
            ).fetchall()
        event = dict(row)
        event["files"] = [dict(f) for f in files]
        return event

    def find_file(self, event_id: str, camera: str, minute_ts: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT camera, minute_ts, filename, size FROM files"
                " WHERE event_id=? AND camera=? AND minute_ts=?",
                (event_id, camera, minute_ts),
            ).fetchone()
        return dict(row) if row else None

    def stats(self) -> dict:
        with self._lock:
            per_folder = {
                r["folder"]: r["n"]
                for r in self._conn.execute(
                    "SELECT folder, COUNT(*) AS n FROM events GROUP BY folder"
                ).fetchall()
            }
            total_files = self._conn.execute(
                "SELECT COUNT(*) AS n FROM files"
            ).fetchone()["n"]
            last_event = self._conn.execute(
                "SELECT MAX(event_ts) AS ts FROM events"
            ).fetchone()["ts"]
        return {"per_folder": per_folder, "total_files": total_files, "last_event_ts": last_event}

    def to_event(self, row: dict) -> Event:
        """Rehydrate a full Event (with files) from a get_event() dict."""
        ev = Event(
            event_id=row["event_id"], folder=row["folder"], event_ts=row["event_ts"],
            reason=row["reason"], city=row["city"], est_lat=row["est_lat"],
            est_lon=row["est_lon"], thumb_present=bool(row["thumb_present"]),
        )
        ev.files = [
            CameraFile(camera=f["camera"], minute_ts=f["minute_ts"],
                       filename=f["filename"], size=f["size"], path=f.get("path", ""))
            for f in row.get("files", [])
        ]
        return ev
