"""Permanent deletion of events: removes video files from disk and drops the index row.

SavedClips/SentryClips events own a real event directory (`indexer.EVENT_FOLDERS`), so
deleting one is a single `rmtree`. RecentClips events are a synthetic per-minute group
whose files may share a directory with *other* RecentClips events' clips (e.g. a
`RecentClips/<date>/` folder), so each file is unlinked individually — never `rmtree`'d.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from .cache import CacheManager
from .config import Settings
from .db import Database
from .indexer import EVENT_FOLDERS

log = logging.getLogger("teslausb_viewer.delete")


def _resolve_within(root: Path, rel_path: str) -> Path | None:
    """Resolve `rel_path` under `root`, or None if it would escape `root`.

    Same containment guard `api.video()` uses — event rows only ever come from the DB, but
    this keeps a corrupted/hand-edited row from deleting outside teslacam_path.
    """
    candidate = (root / rel_path).resolve()
    if not candidate.is_relative_to(root.resolve()):
        return None
    return candidate


def _delete_event_files(settings: Settings, row: dict) -> None:
    root = settings.teslacam_path
    if row["folder"] in EVENT_FOLDERS:
        target = _resolve_within(root, row["event_id"])
        if target is None:
            raise ValueError("event path escapes teslacam_path")
        if target.is_dir():
            shutil.rmtree(target)
        return
    for f in row["files"]:
        rel_path = f.get("path") or f"{row['event_id']}/{f['filename']}"
        target = _resolve_within(root, rel_path)
        if target is None:
            raise ValueError("file path escapes teslacam_path")
        target.unlink(missing_ok=True)


def delete_events(
    settings: Settings, db: Database, cache: CacheManager, event_ids: list[str]
) -> dict:
    """Delete each event's files and index row. Partial-success: one bad id doesn't abort
    the rest. Synchronous (filesystem + sqlite) — call via asyncio.to_thread."""
    deleted: list[str] = []
    failed: list[dict] = []
    for event_id in event_ids:
        row = db.get_event(event_id)
        if not row:
            failed.append({"event_id": event_id, "error": "not found"})
            continue
        try:
            _delete_event_files(settings, row)
        except (OSError, ValueError) as exc:
            log.warning("Delete failed for %s: %s", event_id, exc)
            failed.append({"event_id": event_id, "error": str(exc)})
            continue
        db.delete_event(event_id)
        thumb = cache.thumb_path(event_id)
        if thumb.is_file():
            try:
                thumb.unlink()
            except OSError:
                pass
        deleted.append(event_id)
    return {"deleted": deleted, "failed": failed}
