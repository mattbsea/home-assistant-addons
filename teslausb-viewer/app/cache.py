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
