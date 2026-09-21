"""Append-only persistent capture of ingested telemetry records.

The live records file lives on /tmp (tmpfs) and is truncated at boot, so every past drive is lost on
restart. This keeps a durable copy on the persistent /data volume so drives and field behavior (e.g.
how Gear transitions on park) can be parsed after the fact.

The file is APPEND-ONLY at the JSON-record level: no record is ever rewritten, reordered, or dropped
by the add-on. It IS, however, size-rotated: once it reaches `max_bytes` (default 500 MiB), the
current file is gzip-compressed alongside itself with a UTC timestamp suffix (never deleted — that's
left to the operator) and a fresh file is started at the original path. It is otherwise untouched —
no truncation before the threshold, no time-based rotation, no deletion on any schedule. Rotation
happens on the ingest thread (see main.py's start_ingest), so it briefly pauses record processing;
this is acceptable since it fires roughly once per ~500 MiB of growth, not per write.

Writes are best-effort: a logging failure must never take down the ingest tail that feeds the app.
"""
import gzip
import json
import os
import shutil
import time

DEFAULT_MAX_BYTES = 500 * 1024 * 1024  # 500 MiB


class RecordLog:
    def __init__(self, path, max_bytes=DEFAULT_MAX_BYTES):
        self.path = path
        self.max_bytes = max_bytes
        self._fh = None
        self._size = 0

    def _open(self):
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        # "a" never truncates and always writes at EOF; line-buffered so each record is flushed.
        self._fh = open(self.path, "a", buffering=1)
        try:
            self._size = os.fstat(self._fh.fileno()).st_size
        except OSError:
            self._size = 0

    def write(self, rec):
        """Append one record as a compact JSON line. Rotates first if oversized; never raises."""
        try:
            if self._fh is None:
                self._open()
            if self.max_bytes and self._size >= self.max_bytes:
                self._rotate()
            line = json.dumps(rec, separators=(",", ":"), default=str) + "\n"
            self._fh.write(line)
            self._size += len(line.encode("utf-8"))
        except Exception:
            # Drop this line and reset so a transient error self-heals on the next write.
            self._close_quietly()

    def _rotate(self):
        """Close the current file, gzip-archive it alongside itself with a timestamp suffix, and
        reopen a fresh file at the original path. Best-effort: if archiving fails (e.g. disk full),
        the next write just reopens and keeps appending to the same oversized file rather than
        losing data — this is a size cap for operator convenience, not a hard limit to enforce."""
        self._close_quietly()
        try:
            if os.path.getsize(self.path) > 0:
                archive = f"{self.path}.{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.gz"
                with open(self.path, "rb") as src, gzip.open(archive, "wb", compresslevel=6) as dst:
                    shutil.copyfileobj(src, dst)
                os.remove(self.path)
        except OSError:
            pass
        self._open()

    def _close_quietly(self):
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:
                pass
        self._fh = None
