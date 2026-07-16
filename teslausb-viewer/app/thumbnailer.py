"""Generate a poster-frame thumbnail for events that have no Tesla thumb.png.

TeslaUSB only writes thumb.png for SavedClips/SentryClips events; RecentClips (and any
event whose thumbnail hasn't uploaded yet) have none, so the grid would show a placeholder.
For those we grab one frame from the event's front-camera clip, read directly off
teslacam_path (no network pull), with ffmpeg and store it in the same on-disk thumb cache
get_thumb() already serves from — so no API change is needed.

Runs at scan time (eager), is idempotent (skips events already thumbed), bounds work per
pass, and prunes thumbnails for events that have rolled out of the index (RecentClips is a
rolling buffer, so without pruning its generated thumbs would accumulate forever).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .cache import CacheManager
from .config import Settings
from .db import Database
from .models import CAMERAS

log = logging.getLogger("teslausb_viewer.thumbnailer")

# Bound the work per scan so a large first run can't stall the scan loop; the remainder is
# picked up on subsequent passes (and logged, so the cap is never silent).
MAX_PER_SCAN = 100
FRAME_AT_SECONDS = 1   # grab ~1s in to skip a black/again-starting leading frame
THUMB_WIDTH = 480      # scaled-down poster frame; height auto (kept even for the encoder)


async def backfill(settings: Settings, db: Database, cache: CacheManager) -> int:
    """Generate missing thumbnails for events without a Tesla thumb. Returns count generated."""
    if not settings.has_backend():
        return 0
    candidates = await asyncio.to_thread(db.event_ids_without_thumb)
    pending = [eid for eid in candidates if not cache.has_thumb(eid)]
    generated = 0
    for eid in pending[:MAX_PER_SCAN]:
        row = await asyncio.to_thread(db.get_event, eid)
        if not row:
            continue
        try:
            if await _generate(settings, cache, eid, row.get("files", [])):
                generated += 1
        except Exception:  # noqa: BLE001 — a bad clip must never break the scan
            log.exception("Thumbnail generation failed for %s", eid)
    deferred = max(0, len(pending) - MAX_PER_SCAN)
    if deferred:
        log.info("Thumbnails: generated %d this pass, %d deferred to next scan", generated, deferred)
    elif generated:
        log.info("Thumbnails: generated %d", generated)
    await asyncio.to_thread(_prune_orphans, db, cache)
    return generated


def _pick_clip(files: list[dict]) -> dict | None:
    """Prefer the front camera; fall back to the first clip that has a usable remote path."""
    usable = [f for f in files if f.get("path")]
    if not usable:
        return None
    for cam in CAMERAS:
        for f in usable:
            if f.get("camera") == cam:
                return f
    return usable[0]


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


async def _extract_frame(src: Path, dest: Path) -> bool:
    """Write one scaled frame of `src` to `dest` (PNG) via ffmpeg. False on any failure."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    stderr = b""
    # Try ~1s in to skip a black lead-in; retry at 0s for very short clips.
    for ss in (str(FRAME_AT_SECONDS), "0"):
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-nostdin", "-y", "-ss", ss, "-i", str(src),
            "-frames:v", "1", "-vf", f"scale={THUMB_WIDTH}:-2", str(dest),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0:
            return True
    log.warning("ffmpeg could not extract a frame from %s: %s",
                src.name, stderr.decode("utf-8", "replace").strip()[:200])
    return False


def _prune_orphans(db: Database, cache: CacheManager) -> None:
    """Drop cached thumbnails whose event no longer exists (e.g. rolled-out RecentClips)."""
    valid = {cache.key(eid) for eid in db.all_event_ids()}
    cache.prune_thumbs(valid)
