"""Thin async wrapper around the rclone CLI.

rclone is the universal read layer: the same calls work whether the TeslaUSB backend
is S3/MinIO, Google Drive, Dropbox, OneDrive, B2, SMB/CIFS, SFTP or WebDAV. We only ever
read — listing, fetching small JSON, and copying a clicked event's clips into a local
cache. No `rclone mount` (FUSE needs elevated container privileges).
"""

from __future__ import annotations

import asyncio
import json
import logging

from .config import Settings

log = logging.getLogger("teslausb_viewer.rclone")

# Cap concurrent rclone subprocesses so a burst of thumbnail/listing calls can't fork-bomb.
_semaphore = asyncio.Semaphore(6)


class RcloneError(RuntimeError):
    def __init__(self, args: list[str], code: int, stderr: str):
        self.code = code
        self.stderr = stderr
        super().__init__(f"rclone {' '.join(args)} exited {code}: {stderr.strip()[:500]}")


async def _run(settings: Settings, args: list[str], timeout: float = 120.0) -> bytes:
    """Run `rclone --config <conf> <args>` and return stdout, raising on failure."""
    cmd = ["rclone", "--config", str(settings.rclone_conf), *args]
    async with _semaphore:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RcloneError(args, -1, "timed out")
    if proc.returncode != 0:
        raise RcloneError(args, proc.returncode or -1, stderr.decode("utf-8", "replace"))
    return stdout


def _target(settings: Settings, subpath: str = "") -> str:
    base = settings.remote_base()
    subpath = subpath.strip("/")
    return f"{base}/{subpath}" if subpath else base


async def lsjson(
    settings: Settings,
    subpath: str = "",
    *,
    dirs_only: bool = False,
    files_only: bool = False,
) -> list[dict]:
    """List a remote directory as structured entries (name, IsDir, Size, ModTime)."""
    args = ["lsjson", _target(settings, subpath)]
    if dirs_only:
        args.append("--dirs-only")
    if files_only:
        args.append("--files-only")
    try:
        out = await _run(settings, args)
    except RcloneError as exc:
        # A missing folder (e.g. no SentryClips yet) is normal — return empty.
        if "directory not found" in exc.stderr.lower() or "not found" in exc.stderr.lower():
            return []
        raise
    return json.loads(out or b"[]")


async def cat(settings: Settings, subpath: str, *, max_bytes: int = 1_000_000) -> bytes:
    """Fetch a small file (event.json / thumb.png) straight to memory."""
    return await _run(settings, ["cat", "--count", str(max_bytes), _target(settings, subpath)])


async def copy_to(settings: Settings, subpath: str, dest_dir: str, *, includes: list[str]) -> None:
    """Copy an event folder's clips/thumbnail into a local cache directory."""
    args = ["copy", _target(settings, subpath), dest_dir, "--transfers", "6"]
    for pattern in includes:
        args += ["--include", pattern]
    await _run(settings, args, timeout=600.0)


async def about(settings: Settings) -> dict | None:
    """Backend free/used bytes. Returns None when the remote doesn't support `about` (many S3)."""
    name = settings.resolved_remote_name()
    if not name:
        return None
    try:
        out = await _run(settings, ["about", f"{name}:", "--json"], timeout=30.0)
    except RcloneError as exc:
        log.info("rclone about unavailable for this remote: %s", exc.stderr.strip()[:200])
        return None
    try:
        return json.loads(out or b"{}")
    except json.JSONDecodeError:
        return None
