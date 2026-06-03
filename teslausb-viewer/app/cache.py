"""On-demand per-event video cache with an LRU size cap.

When the user opens an event we `rclone copy` its clips (a minute of 4-6 cameras, tens of
MB) into /data/cache/<key>/ and then serve the local files with HTTP Range support. This
avoids FUSE entirely. Preparing is async with a small state machine the frontend polls;
once total cache size exceeds cache_size_mb, least-recently-accessed events are evicted
(never the one currently preparing or just made ready).
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from . import rclone
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
        self._status: dict[str, dict] = {}  # event_id -> {state, ready, total, error?}
        self._tasks: dict[str, asyncio.Task] = {}
        self._atime: dict[str, float] = {}
        self._lock = asyncio.Lock()

    # --- event video preparation -------------------------------------------
    def event_dir(self, event_id: str) -> Path:
        return self.root / _key(event_id)

    def status(self, event_id: str) -> dict:
        st = self._status.get(event_id)
        if st:
            return st
        # If files already exist on disk from a prior run, treat as ready.
        d = self.event_dir(event_id)
        if d.is_dir() and any(d.glob("*.mp4")):
            return {"state": "ready", "ready": len(list(d.glob("*.mp4")))}
        return {"state": "idle", "ready": 0}

    async def prepare(self, event_id: str, files: list[dict]) -> dict:
        """Idempotently ensure an event's clips are cached. Returns current status.

        `files` is the event's file rows (each with a remote `path` and `filename`).
        """
        async with self._lock:
            st = self.status(event_id)
            if st["state"] in ("ready", "preparing"):
                return st
            self._status[event_id] = {"state": "preparing", "ready": 0, "total": len(files)}
            self._tasks[event_id] = asyncio.create_task(self._copy(event_id, files))
            return self._status[event_id]

    async def _copy(self, event_id: str, files: list[dict]) -> None:
        dest = self.event_dir(event_id)
        dest.mkdir(parents=True, exist_ok=True)
        try:
            items = [(f["path"], f["filename"]) for f in files if f.get("path")]
            if items:
                # Fetch each clip by its exact remote path, flattened to dest/<basename>.
                # Works for both folder-shaped events and scattered RecentClips minutes.
                await rclone.copy_files(self.settings, items, str(dest))
            else:
                # Legacy index rows written before clip paths were recorded: copy the
                # event folder wholesale (correct for SavedClips/SentryClips events).
                await rclone.copy_to(
                    self.settings, event_id, str(dest), includes=["*.mp4", "thumb.png"],
                )
            ready = len(list(dest.glob("*.mp4")))
            self._status[event_id] = {"state": "ready", "ready": ready, "total": len(files)}
            self._atime[event_id] = time.time()
            log.info("Cached event %s (%d clips)", event_id, ready)
        except rclone.RcloneError as exc:
            self._status[event_id] = {"state": "error", "ready": 0, "error": exc.stderr.strip()[:300]}
            log.warning("Failed to cache %s: %s", event_id, exc.stderr.strip()[:200])
        finally:
            self._tasks.pop(event_id, None)
            await self._evict_if_needed()

    def file_path(self, event_id: str, filename: str) -> Path | None:
        """Local path of a cached clip, or None if not present. Touches LRU access time."""
        path = self.event_dir(event_id) / filename
        if path.is_file():
            self._atime[event_id] = time.time()
            return path
        return None

    async def _evict_if_needed(self) -> None:
        cap = self.settings.cache_size_mb * 1024 * 1024
        reserved = {THUMB_DIR_NAME, self.thumb_src_dir().name}
        dirs = [d for d in self.root.iterdir() if d.is_dir() and d.name not in reserved]
        sizes = {d: _dir_size(d) for d in dirs}
        total = sum(sizes.values())
        if total <= cap:
            return
        protected = set(self._tasks.keys())  # currently preparing
        # Oldest access first.
        ordered = sorted(dirs, key=lambda d: self._atime.get(d.name.replace("__", "/"), 0.0))
        for d in ordered:
            if total <= cap:
                break
            eid = d.name.replace("__", "/")
            if eid in protected:
                continue
            freed = sizes.get(d, 0)
            _rmtree(d)
            self._status.pop(eid, None)
            self._atime.pop(eid, None)
            total -= freed
            log.info("Evicted cached event %s (%d bytes)", eid, freed)

    # --- thumbnails ---------------------------------------------------------
    def key(self, event_id: str) -> str:
        """Cache-safe key for an event id (shared with the thumbnailer)."""
        return _key(event_id)

    def thumb_path(self, event_id: str) -> Path:
        """On-disk location of an event's thumbnail (Tesla-fetched or ffmpeg-generated)."""
        return self.root / THUMB_DIR_NAME / f"{_key(event_id)}.png"

    def has_thumb(self, event_id: str) -> bool:
        return self.thumb_path(event_id).is_file()

    def thumb_src_dir(self) -> Path:
        """Disk-backed scratch dir for clips pulled to extract a frame (never /tmp/tmpfs)."""
        return self.root / ".thumb_src"

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
        """Return thumbnail bytes, fetching+caching the Tesla thumb on first request. None if absent.

        A previously generated frame (thumbnailer) lives at the same path, so it is served
        here too without a backend round-trip.
        """
        cached = self.thumb_path(event_id)
        if cached.is_file():
            return cached.read_bytes()
        try:
            data = await rclone.cat(self.settings, f"{event_id}/thumb.png", max_bytes=2_000_000)
        except rclone.RcloneError:
            return None
        if data:
            cached.write_bytes(data)
        return data or None

    def total_bytes(self) -> int:
        return _dir_size(self.root)


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def _rmtree(path: Path) -> None:
    for p in sorted(path.rglob("*"), reverse=True):
        try:
            p.unlink() if p.is_file() else p.rmdir()
        except OSError:
            pass
    try:
        path.rmdir()
    except OSError:
        pass


def order_cameras(cameras: list[str]) -> list[str]:
    """Stable camera ordering for the player grid (known order, then any extras)."""
    known = [c for c in CAMERAS if c in cameras]
    extra = [c for c in cameras if c not in CAMERAS]
    return known + extra
