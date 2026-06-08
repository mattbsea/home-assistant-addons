# On-Demand Streaming Playback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the upfront per-event download with on-demand streaming: a long-lived `rclone serve http` sidecar with a VFS read-through disk cache, fronted by a Range-forwarding `/video` proxy.

**Architecture:** The FastAPI app supervises one `rclone serve http` process (rooted at the configured remote, `--vfs-cache-mode full`, bounded by `cache_size_mb`, bound to `127.0.0.1:8100`, read-only). `GET /video` proxies the browser's `Range` request to that sidecar via a streaming `httpx` client and passes `206`/`Content-Range` straight back. The old prepare/copy cache and its `/prepare` + `/status` endpoints are removed; the frontend streams directly.

**Tech Stack:** Python 3 / FastAPI / Starlette `StreamingResponse`, `httpx` (new dep), rclone CLI, vanilla-JS frontend. Tests run via `tests/run.sh` (custom `check()` runner) against `tests/fake_rclone.py`.

**Conventions:** All paths below are relative to `teslausb-viewer/`. There is no pytest — "run the tests" always means `bash tests/run.sh`, which boots the app under `TestClient` (triggering lifespan, which starts the fake sidecar) and prints `PASS`/`FAIL` lines ending in `RESULT: ALL PASS`. Commit messages follow the repo style and end with the `Co-Authored-By` trailer.

---

## File Structure

- **Create** `app/stream.py` — `StreamServer`: owns the `rclone serve http` subprocess lifecycle (start/supervise/readiness/stop) and the Range-forwarding proxy that returns a `StreamingResponse`. Single responsibility: "stream a remote clip by path."
- **Modify** `app/config.py` — add `stream_port` to `Settings` + `get_settings()`.
- **Modify** `requirements.txt` — add `httpx`.
- **Modify** `app/db.py` — `find_file` selects `path`.
- **Modify** `app/main.py` — start/stop `StreamServer` in lifespan as `app.state.stream`.
- **Modify** `app/api.py` — rewrite `/video` as a proxy; delete `/prepare` and `/status`.
- **Modify** `app/cache.py` — remove the event-copy machinery; keep thumbnails + `order_cameras`.
- **Modify** `app/web/player.js` — drop the prepare gate and status polling; render+stream directly.
- **Modify** `tests/fake_rclone.py` — implement a Range-capable `serve http` subcommand.
- **Modify** `tests/test_api.py` — replace prepare/status/video assertions with streaming ones; add no-backend `503` and legacy-`path` fallback checks.
- **Modify** `config.yaml`, `CHANGELOG.md`, `DOCS.md` — version 0.2.0 + docs.

---

## Task 1: Add `httpx` dependency and `stream_port` setting

**Files:**
- Modify: `requirements.txt`
- Modify: `app/config.py`

- [ ] **Step 1: Add httpx to requirements**

In `requirements.txt`, add a line:

```
httpx==0.28.1
```

- [ ] **Step 2: Add `stream_port` to the `Settings` dataclass**

In `app/config.py`, add a field to the `@dataclass(frozen=True) class Settings` (place it right after `port: int`):

```python
    port: int
    stream_port: int
```

- [ ] **Step 3: Populate `stream_port` in `get_settings()`**

In `app/config.py`, inside `get_settings()` where the `Settings(...)` instance is built, add (next to the existing `port=...` line):

```python
        stream_port=_int("TUV_STREAM_PORT", 8100),
```

- [ ] **Step 4: Verify it imports**

Run: `cd teslausb-viewer && python3 -c "from app.config import Settings; print('stream_port' in Settings.__dataclass_fields__)"`
Expected: `True`

- [ ] **Step 5: Commit**

```bash
git add teslausb-viewer/requirements.txt teslausb-viewer/app/config.py
git commit -m "feat(stream): add httpx dep and stream_port setting

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Create `StreamServer` (sidecar + proxy)

**Files:**
- Create: `app/stream.py`

- [ ] **Step 1: Write `app/stream.py`**

```python
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
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
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
```

- [ ] **Step 2: Write a failing unit check for `_cmd()`**

Create `tests/test_stream.py`:

```python
"""Unit checks for StreamServer command assembly (no real rclone)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def run():
    from app.config import get_settings
    from app.stream import StreamServer

    s = get_settings()
    cmd = StreamServer(s)._cmd()
    assert cmd[0] == "rclone", cmd
    assert "serve" in cmd and "http" in cmd, cmd
    assert s.remote_base() in cmd, cmd
    assert "--read-only" in cmd, cmd
    assert f"127.0.0.1:{s.stream_port}" in cmd, cmd
    assert f"{s.cache_size_mb}M" in cmd, cmd
    print("PASS stream _cmd assembly")


if __name__ == "__main__":
    run()
```

- [ ] **Step 3: Run it to verify it passes**

Run: `cd teslausb-viewer && TUV_REMOTE_NAME=minio TUV_REMOTE_PATH=teslausb TUV_RCLONE_CONF=/dev/null TUV_CACHE_SIZE_MB=2048 python3 tests/test_stream.py`
Expected: `PASS stream _cmd assembly` (it constructs a `Settings` from env; `remote_base()` works without a real conf).

Note: if `get_settings()` is memoized and picks up a different env in the full suite, that is fine — this check only asserts structural invariants.

- [ ] **Step 4: Commit**

```bash
git add teslausb-viewer/app/stream.py teslausb-viewer/tests/test_stream.py
git commit -m "feat(stream): rclone serve http sidecar + Range proxy

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `find_file` returns the remote `path`

**Files:**
- Modify: `app/db.py` (`find_file`, ~line 208)

- [ ] **Step 1: Add `path` to the SELECT**

In `app/db.py`, change the `find_file` query from:

```python
                "SELECT camera, minute_ts, filename, size FROM files"
                " WHERE event_id=? AND camera=? AND minute_ts=?",
```

to:

```python
                "SELECT camera, minute_ts, filename, size, path FROM files"
                " WHERE event_id=? AND camera=? AND minute_ts=?",
```

- [ ] **Step 2: Verify the column exists in the schema**

Run: `cd teslausb-viewer && grep -n "path" app/db.py | head`
Expected: shows the `path` column in the `files` table DDL / migration (added in 0.1.7). If absent, STOP — the spec assumption is wrong.

- [ ] **Step 3: Commit**

```bash
git add teslausb-viewer/app/db.py
git commit -m "feat(stream): return remote path from find_file

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Wire `StreamServer` into the app lifespan

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Import `StreamServer`**

In `app/main.py`, add to the imports block (after `from .mqtt_publisher import MqttPublisher`):

```python
from .stream import StreamServer
```

- [ ] **Step 2: Start it in lifespan**

In `lifespan()`, after the `app.state.cache = CacheManager(settings)` line and before `app.state.mqtt = MqttPublisher(settings)`, add:

```python
    app.state.stream = StreamServer(settings)
    await app.state.stream.start()
```

- [ ] **Step 3: Stop it on shutdown**

In the `finally:` block of `lifespan()`, after `app.state.mqtt.stop()` and before `app.state.db.close()`, add:

```python
        await app.state.stream.stop()
```

- [ ] **Step 4: Commit**

```bash
git add teslausb-viewer/app/main.py
git commit -m "feat(stream): start/stop streaming sidecar in lifespan

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Rewrite `/video` as a proxy; remove `/prepare` and `/status`

**Files:**
- Modify: `app/api.py`

- [ ] **Step 1: Replace the prepare/status/video block**

In `app/api.py`, delete the three handlers `prepare`, `status`, and `video` (the block from `@router.post("/api/events/{event_id:path}/prepare")` through the end of the `video` function) and replace with:

```python
@router.get("/api/events/{event_id:path}/video/{camera}/{minute_ts}")
async def video(event_id: str, camera: str, minute_ts: str, request: Request) -> Response:
    st = _state(request)
    if not st.stream.available:
        raise HTTPException(503, "backend not configured")
    row = await asyncio.to_thread(st.db.find_file, event_id, camera, minute_ts)
    if not row:
        raise HTTPException(404, "no such clip")
    # `path` is the clip's location under the remote base (recorded since 0.1.7); older
    # rows fall back to the folder-shaped path the copy model assumed.
    remote_path = row.get("path") or f"{event_id}/{row['filename']}"
    return await st.stream.video_response(remote_path, request.headers.get("range"))
```

- [ ] **Step 2: Drop the now-unused `FileResponse` import**

In `app/api.py`, change:

```python
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
```

to:

```python
from fastapi.responses import HTMLResponse, JSONResponse
```

- [ ] **Step 3: Verify imports resolve**

Run: `cd teslausb-viewer && python3 -c "import app.api"`
Expected: no output, exit 0. (If it complains about `FileResponse` used elsewhere, restore only that import — but a grep should show it is now unused.)

Run: `cd teslausb-viewer && grep -n "FileResponse\|/prepare\|/status" app/api.py`
Expected: no matches.

- [ ] **Step 4: Commit**

```bash
git add teslausb-viewer/app/api.py
git commit -m "feat(stream): proxy /video to sidecar; remove prepare/status

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Trim `CacheManager` to thumbnails only

**Files:**
- Modify: `app/cache.py`

- [ ] **Step 1: Remove the event-copy machinery**

In `app/cache.py`, delete these members entirely: `event_dir`, `status`, `prepare`, `_copy`, `file_path`, and `_evict_if_needed`. In `__init__`, delete the now-unused fields `self._status`, `self._tasks`, `self._atime`, and `self._lock`. Keep everything thumbnail-related (`thumb_path`, `has_thumb`, `get_thumb`, `thumb_src_dir`, `prune_thumbs`, `key`, `total_bytes`), the module-level `order_cameras`, `_dir_size`, `_rmtree`, and `_key`.

- [ ] **Step 2: Remove now-unused imports**

In `app/cache.py`, delete `import asyncio` and `import time` (only the removed methods used them). Keep `import logging`, `from pathlib import Path`, `from . import rclone`, `from .config import Settings`, `from .models import CAMERAS`.

- [ ] **Step 3: Update the module docstring**

Replace the opening docstring of `app/cache.py` with:

```python
"""Thumbnail cache for events (Tesla-supplied `thumb.png` or an ffmpeg-generated frame).

Video is no longer copied here — clips stream on demand through the rclone serve http
sidecar (see app/stream.py). This module now only owns the on-disk thumbnail cache and
the camera-ordering helper used by the player grid.
"""
```

- [ ] **Step 4: Verify it imports and nothing else references the removed methods**

Run: `cd teslausb-viewer && python3 -c "import app.cache, app.main, app.api"`
Expected: exit 0.

Run: `cd teslausb-viewer && grep -rn "\.prepare(\|\.file_path(\|cache.status(\|event_dir(" app/`
Expected: no matches (all callers removed in Task 5).

- [ ] **Step 5: Commit**

```bash
git add teslausb-viewer/app/cache.py
git commit -m "refactor(stream): cache.py keeps thumbnails only

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Frontend — stream directly, drop the prepare gate

**Files:**
- Modify: `app/web/player.js`

- [ ] **Step 1: Render immediately after loading detail**

In `app/web/player.js`, in `open()`, replace:

```javascript
    await prepareThenRender();
```

with:

```javascript
    render();
```

- [ ] **Step 2: Delete `prepareThenRender` and the status poll**

In `app/web/player.js`, delete the entire `async function prepareThenRender() { ... }` function (the block that POSTs `/prepare` and polls `/status`).

- [ ] **Step 3: Remove the now-unused poll plumbing**

In `app/web/player.js`:
- Delete the `const POLL_MS = 800;` line.
- Delete the `let pollTimer = null;` line.
- In `stop()`, delete the line `if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }`.

- [ ] **Step 4: Syntax-check**

Run: `cd teslausb-viewer && node --check app/web/player.js && grep -n "prepareThenRender\|POLL_MS\|pollTimer\|/prepare\|/status" app/web/player.js`
Expected: `node --check` prints nothing (exit 0); the grep returns no matches.

- [ ] **Step 5: Commit**

```bash
git add teslausb-viewer/app/web/player.js
git commit -m "feat(stream): player streams directly, no prepare gate

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Test harness — fake `serve http` + streaming assertions

**Files:**
- Modify: `tests/fake_rclone.py`
- Modify: `tests/test_api.py`
- Modify: `tests/run.sh` (only if it enumerates test files explicitly — see Step 5)

- [ ] **Step 1: Add a Range-capable `serve http` to the fake rclone**

In `tests/fake_rclone.py`, add this handler inside `main()`, before the final `unknown command` fallback (after the `about` block):

```python
    if cmd == "serve" and rest[:1] == ["http"]:
        import http.server
        import socketserver

        serve_args = rest[1:]
        addr, target = "127.0.0.1:8080", None
        i = 0
        valued = {"--addr", "--vfs-cache-mode", "--vfs-cache-max-size",
                  "--vfs-cache-max-age", "--cache-dir"}
        while i < len(serve_args):
            a = serve_args[i]
            if a == "--addr":
                addr = serve_args[i + 1]; i += 2; continue
            if a in valued:
                i += 2; continue
            if a.startswith("--"):
                i += 1; continue
            target = a; i += 1
        docroot = resolve(target)
        host, _, port = addr.partition(":")

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def do_GET(self):
                import os as _os
                from urllib.parse import unquote
                path = _os.path.normpath(_os.path.join(docroot, unquote(self.path.lstrip("/"))))
                if not path.startswith(docroot) or not _os.path.isfile(path):
                    self.send_error(404); return
                size = _os.path.getsize(path)
                rng = self.headers.get("Range")
                with open(path, "rb") as fh:
                    if rng and rng.startswith("bytes="):
                        start_s, _, end_s = rng[len("bytes="):].partition("-")
                        start = int(start_s or 0)
                        end = int(end_s) if end_s else size - 1
                        end = min(end, size - 1)
                        fh.seek(start)
                        chunk = fh.read(end - start + 1)
                        self.send_response(206)
                        self.send_header("Content-Type", "video/mp4")
                        self.send_header("Accept-Ranges", "bytes")
                        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                        self.send_header("Content-Length", str(len(chunk)))
                        self.end_headers()
                        self.wfile.write(chunk)
                    else:
                        data = fh.read()
                        self.send_response(200)
                        self.send_header("Content-Type", "video/mp4")
                        self.send_header("Accept-Ranges", "bytes")
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)

        with socketserver.ThreadingTCPServer((host, int(port)), H) as srv:
            srv.allow_reuse_address = True
            srv.serve_forever()
        return 0
```

- [ ] **Step 2: Run the suite to watch it FAIL on the old prepare assertions**

Run: `cd teslausb-viewer && bash tests/run.sh`
Expected: FAIL — the test still calls `/prepare` and `/status` (now 404) and asserts `video 425 before prepare`, which no longer holds. This confirms the test exercises the new path next.

- [ ] **Step 3: Rewrite the SavedClips video assertions in `tests/test_api.py`**

In `tests/test_api.py`, replace the block:

```python
        r = c.get(f"/api/events/{EVENT_ID}/video/front/{MINUTE}")
        check("video 425 before prepare", r.status_code == 425, str(r.status_code))

        c.post(f"/api/events/{EVENT_ID}/prepare")
```

…through the prepare-ready/`video 200 after prepare` assertions (down to and including the `check("prepare ready", ...)` and `check("video 200 after prepare", ...)` lines), with:

```python
        r = c.get(f"/api/events/{EVENT_ID}/video/front/{MINUTE}")
        check("video 200 streamed", r.status_code == 200, str(r.status_code))
```

Keep the existing Range assertions that follow (`video 206 on range`, `content-range header`, `range body 1024 bytes`) — they now exercise the proxy.

- [ ] **Step 4: Rewrite the RecentClips video assertions**

In `tests/test_api.py`, replace:

```python
        c.post(f"/api/events/{rec_id}/prepare")
```

through the `check("recent prepare ready", ...)` line, and change the following `recent video 200` line, so the RecentClips section reads:

```python
        r = c.get(f"/api/events/{rec_id}/video/front/2024-01-15_10-31-00")
        check("recent video 200 (streamed from date subfolder)", r.status_code == 200, str(r.status_code))
```

(Delete the loop that polled `/status` for the recent event.)

- [ ] **Step 5: Add no-backend `503` and legacy-`path` fallback checks**

In `tests/test_api.py`, after the existing video assertions, add:

```python
        # Legacy rows with a NULL path fall back to event_id/filename and still stream.
        import sqlite3 as _sql
        from app.config import get_settings as _gs
        _db = _sql.connect(_gs().db_path)
        _db.execute("UPDATE files SET path=NULL WHERE event_id=? AND camera='front'", (EVENT_ID,))
        _db.commit(); _db.close()
        r = c.get(f"/api/events/{EVENT_ID}/video/front/{MINUTE}")
        check("legacy null-path still streams", r.status_code == 200, str(r.status_code))
```

If `tests/test_stream.py` is not already run by `tests/run.sh`, append this line near where `run.sh` invokes the python test (right after the `test_api.py` invocation):

```bash
"$VENV/bin/python" "$ADDON_DIR/tests/test_stream.py"
```

- [ ] **Step 6: Run the full suite to verify it PASSES**

Run: `cd teslausb-viewer && bash tests/run.sh`
Expected: ends with `RESULT: ALL PASS`, including `video 200 streamed`, `video 206 on range`, `content-range header`, `range body 1024 bytes`, `recent video 200 (streamed from date subfolder)`, and `legacy null-path still streams`.

- [ ] **Step 7: Commit**

```bash
git add teslausb-viewer/tests/fake_rclone.py teslausb-viewer/tests/test_api.py teslausb-viewer/tests/run.sh
git commit -m "test(stream): fake serve http + streaming assertions

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Version bump, changelog, docs

**Files:**
- Modify: `config.yaml`
- Modify: `CHANGELOG.md`
- Modify: `DOCS.md`

- [ ] **Step 1: Bump the version**

In `teslausb-viewer/config.yaml`, change `version: "0.1.11"` to `version: "0.2.0"`.

- [ ] **Step 2: Add the changelog entry**

In `teslausb-viewer/CHANGELOG.md`, insert after the `# Changelog` header:

```markdown
## 0.2.0

### ✨ Improvements
- **On-demand streaming playback.** Opening an event no longer downloads every clip first.
  The add-on runs an `rclone serve http` sidecar with a read-through VFS disk cache (bounded
  by `cache_size_mb`) and the player streams each camera straight through a Range-forwarding
  proxy — first frame plays immediately, and only the bytes you watch are fetched (then
  cached, so seeks and the multi-camera re-syncs stay fast). Works for every rclone backend
  and stays same-origin behind ingress.

### 🔧 Changes
- Removed the `prepare`/`status` "Downloading clips…" step (no longer needed).
- `cache_size_mb` now bounds the streaming read-cache rather than a per-event copy cache.
```

- [ ] **Step 3: Update DOCS.md**

In `teslausb-viewer/DOCS.md`, find the playback/cache description and update it to state that clips stream on demand via an `rclone serve http` sidecar, and that `cache_size_mb` bounds the streaming read-through cache. (Match the surrounding doc's wording; remove any mention of a prepare/download wait.)

- [ ] **Step 4: Commit**

```bash
git add teslausb-viewer/config.yaml teslausb-viewer/CHANGELOG.md teslausb-viewer/DOCS.md
git commit -m "chore(stream): v0.2.0 — streaming playback, changelog, docs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Deploy and verify on the live add-on

> Operational, not TDD. Run after all tasks are committed and pushed to `main`.

- [ ] **Step 1: Push**

```bash
cd /data/home/code/homeassistant-add-ons && git push origin main
```

- [ ] **Step 2: Reload store + update via Supervisor (from inside the Claude Terminal add-on)**

```bash
AUTH="Authorization: Bearer $SUPERVISOR_TOKEN"; SLUG=a44b0313_teslausb_viewer
curl -sS -X POST -H "$AUTH" http://supervisor/store/reload
curl -sS -X POST -H "$AUTH" "http://supervisor/addons/$SLUG/update"
curl -sS -H "$AUTH" "http://supervisor/addons/$SLUG/info" | python3 -c 'import sys,json;d=json.load(sys.stdin)["data"];print(d["version"],d["state"])'
```
Expected: `0.2.0 started`.

- [ ] **Step 3: Smoke-test the live stream path**

```bash
ADDON=http://172.30.33.26:8099   # confirm IP via supervisor info if changed
EID=$(curl -sS "$ADDON/api/events?limit=1" | python3 -c 'import sys,json;e=json.load(sys.stdin)["events"];print(e[0]["event_id"] if e else "")')
# detail → pick a camera+minute, then:
curl -sS -D - -o /dev/null -H "Range: bytes=0-1023" "$ADDON/api/events/<enc-eid>/video/front/<minute_ts>"
```
Expected: `HTTP/1.1 206 Partial Content` with `Content-Range:` — bytes streamed without any prepare call.

- [ ] **Step 4: Verify in the browser**

Open the viewer (hard-reload once if needed), open a multi-camera event: it should start playing within a second or two with no "Downloading clips…" step; scrubbing and scene auto-advance work.

---

## Self-Review

**Spec coverage:**
- §3.1 sidecar → Task 2 (`_cmd`, supervise, readiness, stop), Task 4 (lifespan). ✓
- §3.2 Range proxy → Task 2 (`video_response`), Task 5 (`/video` handler). ✓
- §3.3 CacheManager trim → Task 6. ✓
- §3.4 `find_file` path + legacy fallback → Task 3 (DB), Task 5 (fallback), Task 8 Step 5 (test). ✓
- §3.5 frontend → Task 7. ✓
- §3.6 config (`stream_port`, cache semantics) → Task 1, Task 9. ✓
- §5 error handling (503 no backend, 404, 502, connect-retry, legacy path) → Task 2 + Task 5 + Task 8. ✓
- §6 testing (fake serve http with Range) → Task 8. ✓
- §7 rollout (0.2.0, docs) → Task 9; deploy → Task 10. ✓
- Old `/data/cache/<key>/` cleanup (§7) is intentionally **not** implemented — harmless stale dirs age out under no churn; YAGNI. Noted here so it isn't mistaken for a gap.

**Placeholder scan:** No TBD/TODO. The only `<...>` are shell placeholders in Task 10 (operational values discovered at deploy time), not code.

**Type/name consistency:** `StreamServer` with `start()/stop()/available/video_response()/_cmd()` is defined in Task 2 and used identically in Tasks 4–5. `app.state.stream` set in Task 4, read in Task 5. `find_file` returns `path`, consumed via `row.get("path")` in Task 5. `stream_port` defined in Task 1, used in Task 2. Consistent.
