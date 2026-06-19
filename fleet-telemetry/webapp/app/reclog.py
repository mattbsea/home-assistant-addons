"""Append-only persistent capture of ingested telemetry records.

The live records file lives on /tmp (tmpfs) and is truncated at boot, so every past drive is lost on
restart. This keeps a durable copy on the persistent /data volume so drives and field behavior (e.g.
how Gear transitions on park) can be parsed after the fact.

The file is strictly APPEND-ONLY: it is never rotated, truncated, or deleted by the add-on — not at
boot, not at any size. It is removed only when the add-on is uninstalled (Home Assistant wipes the
/data volume then). Consequently it grows without bound; the operator is responsible for it.

Writes are best-effort: a logging failure must never take down the ingest tail that feeds the app.
"""
import json
import os


class RecordLog:
    def __init__(self, path):
        self.path = path
        self._fh = None

    def _open(self):
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        # "a" never truncates and always writes at EOF; line-buffered so each record is flushed.
        self._fh = open(self.path, "a", buffering=1)

    def write(self, rec):
        """Append one record as a compact JSON line. Never truncates; never raises."""
        try:
            if self._fh is None:
                self._open()
            self._fh.write(json.dumps(rec, separators=(",", ":"), default=str) + "\n")
        except Exception:
            # Drop this line and reset so a transient error self-heals on the next write.
            self._close_quietly()

    def _close_quietly(self):
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:
                pass
        self._fh = None
