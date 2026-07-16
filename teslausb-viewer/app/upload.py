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
