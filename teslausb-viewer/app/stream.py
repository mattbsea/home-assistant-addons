"""rclone serve http sidecar + Range-forwarding proxy.

Runs one long-lived `rclone serve http` rooted at the configured remote, with rclone's
VFS read-through disk cache. The /video endpoint proxies the browser's Range request to
this localhost sidecar and streams the 206 back — so playback is on-demand (no upfront
download) while seeks and the 6-camera drift re-syncs are served from the disk cache.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from .config import Settings

log = logging.getLogger("teslausb_viewer.stream")

_PASSTHROUGH = ("content-type", "content-length", "content-range", "accept-ranges")


class StreamServer:
    """Supervises `rclone serve http` and proxies Range requests to it."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.port = settings.stream_port
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._proc: asyncio.subprocess.Process | None = None
        self._supervisor: asyncio.Task | None = None
        self._client: httpx.AsyncClient | None = None

    def _cmd(self) -> list[str]:
        s = self.settings
        return [
            "rclone", "--config", str(s.rclone_conf),
            "serve", "http", s.remote_base(),
            "--addr", f"127.0.0.1:{self.port}",
            "--read-only",
            "--vfs-cache-mode", "full",
            "--vfs-cache-max-size", f"{s.cache_size_mb}M",
            "--vfs-cache-max-age", "24h",
            "--cache-dir", str(s.cache_dir / ".vfs"),
        ]

    async def start(self) -> None:
        """Start the sidecar (if a backend is configured) and wait until it accepts
        connections, so the first /video request doesn't race the bind."""
        if not self.settings.has_backend():
            log.info("No backend configured; streaming sidecar not started")
            return
        (self.settings.cache_dir / ".vfs").mkdir(parents=True, exist_ok=True)
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))
        self._supervisor = asyncio.create_task(self._supervise())
        await self._await_ready(timeout=15.0)

    async def _supervise(self) -> None:
        backoff = 1
        while True:
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    *self._cmd(),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                log.info("Started rclone serve http on 127.0.0.1:%d", self.port)
                rc = await self._proc.wait()
                log.warning("rclone serve http exited (rc=%s); restarting", rc)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("rclone serve http supervisor error")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _await_ready(self, *, timeout: float) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
                return
            except OSError:
                await asyncio.sleep(0.2)
        log.warning("Streaming sidecar not ready after %.0fs; serving may 503 briefly", timeout)

    async def stop(self) -> None:
        if self._supervisor:
            self._supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._supervisor
        if self._proc and self._proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self._proc.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._proc.wait(), timeout=5)
        if self._client:
            await self._client.aclose()

    @property
    def available(self) -> bool:
        return self._client is not None

    async def video_response(self, remote_path: str, range_header: str | None) -> StreamingResponse:
        """Proxy a GET (optionally Ranged) for `remote_path` to the sidecar and stream it back."""
        if self._client is None:
            raise HTTPException(503, "streaming backend not available")
        url = f"{self.base_url}/{quote(remote_path, safe='/')}"
        headers = {"Range": range_header} if range_header else {}
        req = self._client.build_request("GET", url, headers=headers)
        try:
            upstream = await self._client.send(req, stream=True)
        except httpx.ConnectError:
            raise HTTPException(503, "streaming sidecar starting; retry")
        if upstream.status_code == 404:
            await upstream.aclose()
            raise HTTPException(404, "clip not found on backend")
        if upstream.status_code >= 500:
            await upstream.aclose()
            raise HTTPException(502, "backend stream error")
        out_headers = {k: upstream.headers[k] for k in _PASSTHROUGH if k in upstream.headers}
        out_headers.setdefault("content-type", "video/mp4")

        async def body():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()

        return StreamingResponse(body(), status_code=upstream.status_code, headers=out_headers)
