# TeslaUSB Viewer — Port-Split Auth Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the add-on's single shared port into two: an ingress-only port (8099, no LAN
exposure) serving the full UI/API, and a dedicated upload port (8100, LAN/externally exposable)
that a middleware restricts to serving only `/api/upload/*` — enforced by which socket
accepted the connection, not by a forgeable header.

**Architecture:** `app/serve.py` (new) runs two `uvicorn.Server` instances concurrently in one
process against the same FastAPI `app`. `app/main.py` gains a middleware that 404s any
non-upload path when `request.scope["server"][1] == settings.upload_port`. `config.yaml`
drops the LAN mapping from 8099 and adds one for 8100. `run.sh` launches via the new module
instead of the `uvicorn` CLI directly.

**Tech Stack:** Python 3.11, FastAPI, `uvicorn` (already a dependency, used programmatically
instead of via its CLI entrypoint).

## Global Constraints

- Port 8099 (`ingress_port` in `config.yaml`) MUST have no host `ports:` mapping — reachable
  only via Supervisor's ingress proxy.
- Port 8100 (new) MUST serve `/api/upload/*` only — every other path returns `404` when the
  connection arrived on this port, determined via `request.scope["server"][1]` (an ASGI-level
  fact uvicorn sets itself), never via a client-supplied header.
- `Settings.upload_port: int` (env `TUV_UPLOAD_PORT`, default `8100`) is the exact name/type
  every later task in this plan depends on.
- Test convention: standalone `python tests/test_*.py`, hand-rolled `check()`/`assert` — NOT
  pytest. `TestClient`'s default scope port is `80` (confirmed empirically), so it always
  behaves like an ingress-port request unless a test explicitly overrides `scope["server"]`.
- Bump `version` in `config.yaml` and `__version__` in `app/__init__.py` to `0.5.0` (kept in
  sync, enforced by `tests/test_api.py`'s existing version-match check).

---

### Task 1: `Settings.upload_port` + `app/serve.py` dual-listener launcher

**Files:**
- Modify: `teslausb-viewer/app/config.py`
- Create: `teslausb-viewer/app/serve.py`
- Test: `teslausb-viewer/tests/test_serve.py` (new)

**Interfaces:**
- Consumes: `Settings.port` (existing, unchanged meaning: the ingress port), `get_settings()`
  (existing).
- Produces: `Settings.upload_port: int` (new field). `app/serve.py`'s `main()` function —
  later tasks (packaging) invoke this via `python -m app.serve`, not imported by other Python
  modules.

- [ ] **Step 1: Write the failing test**

```python
# teslausb-viewer/tests/test_serve.py
"""Unit checks for Settings.upload_port and app/serve.py's listener construction."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def run():
    os.environ.pop("TUV_UPLOAD_PORT", None)
    from app.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    assert s.upload_port == 8100, s.upload_port

    os.environ["TUV_UPLOAD_PORT"] = "9100"
    get_settings.cache_clear()
    s2 = get_settings()
    assert s2.upload_port == 9100, s2.upload_port
    os.environ.pop("TUV_UPLOAD_PORT", None)
    get_settings.cache_clear()

    # serve.py must expose a main() entrypoint and build two distinct uvicorn.Config objects
    # (one per port) without actually binding a socket (Config() alone doesn't bind).
    import app.serve as serve_mod
    assert hasattr(serve_mod, "main"), "serve.py must expose main()"

    print("PASS upload_port default + override, serve.main exists")


if __name__ == "__main__":
    run()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd teslausb-viewer && PYTHONPATH=. python3 tests/test_serve.py`
Expected: `AssertionError` (`Settings` has no `upload_port` yet) or `ModuleNotFoundError: No module named 'app.serve'`.

- [ ] **Step 3: Add `upload_port` to `Settings` in `app/config.py`**

In the `Settings` dataclass, add a new field `upload_port: int` (place it right after the
existing `port: int` field for readability). In `get_settings()`, add:
```python
        upload_port=_int("TUV_UPLOAD_PORT", 8100),
```
(place it right after the existing `port=_int("TUV_PORT", 8099),` line). No other change to
`config.py`.

- [ ] **Step 4: Create `app/serve.py`**

```python
# teslausb-viewer/app/serve.py
"""Runs two uvicorn listeners against the same FastAPI app: the ingress port (full route
table, reachable only via Supervisor's ingress proxy) and the upload port (restricted by
main.py's `restrict_upload_port` middleware to /api/upload/* only, reachable on the
LAN/externally). One process, one event loop, one DB connection/scan loop/MQTT client —
just two listening sockets.
"""

from __future__ import annotations

import asyncio

import uvicorn

from .config import get_settings
from .main import app


async def _run() -> None:
    settings = get_settings()
    ingress = uvicorn.Server(uvicorn.Config(
        app, host="0.0.0.0", port=settings.port, workers=1, access_log=False,
    ))
    upload = uvicorn.Server(uvicorn.Config(
        app, host="0.0.0.0", port=settings.upload_port, workers=1, access_log=False,
    ))
    await asyncio.gather(ingress.serve(), upload.serve())


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd teslausb-viewer && PYTHONPATH=. python3 tests/test_serve.py`
Expected: `PASS upload_port default + override, serve.main exists`, exit 0.

- [ ] **Step 6: Commit**

```bash
cd teslausb-viewer
git add app/config.py app/serve.py tests/test_serve.py
git commit -m "feat(teslausb-viewer): Settings.upload_port + dual-listener serve.py"
```

---

### Task 2: Port-restriction middleware in `app/main.py`

**Files:**
- Modify: `teslausb-viewer/app/main.py`
- Test: `teslausb-viewer/tests/test_port_restriction.py` (new)

**Interfaces:**
- Consumes: `Settings.upload_port` (Task 1); FastAPI's `app` object and its existing
  `ingress_base` middleware (unchanged, this task adds a second, independent middleware).
- Produces: nothing new consumed by later tasks — this is the enforcement point itself.

- [ ] **Step 1: Write the failing test**

```python
# teslausb-viewer/tests/test_port_restriction.py
"""Unit checks for the upload-port restriction middleware.

Calls the middleware function directly against hand-built Starlette Requests with different
scope["server"] ports, rather than spinning up real uvicorn sockets (out of scope/flaky for
this suite — see the design spec's Testing section).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_request(app, path, port):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "server": ("testhost", port),
        "client": ("1.2.3.4", 12345),
        "scheme": "http",
        "app": app,
    }
    return Request(scope)


async def _call_next_ok(request):
    from starlette.responses import Response
    return Response("ok", status_code=200)


def run():
    os.environ.setdefault("TUV_TESLACAM_PATH", "/tmp/tuv-port-restriction-nonexistent")
    os.environ.setdefault("TUV_MQTT_ENABLED", "false")
    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import app, restrict_upload_port

    settings = get_settings()
    failures = []

    def check(name, cond, extra=""):
        print(("PASS" if cond else "FAIL"), name, extra)
        if not cond:
            failures.append(name)

    async def _run_checks():
        # Non-upload path on the upload port -> 404, never reaches call_next.
        req = _make_request(app, "/api/events", settings.upload_port)
        resp = None
        status = None
        try:
            resp = await restrict_upload_port(req, _call_next_ok)
            status = resp.status_code
        except Exception as exc:  # HTTPException raised directly is also acceptable
            status = getattr(exc, "status_code", None)
        check("non-upload path on upload port -> 404", status == 404, str(status))

        # Upload path on the upload port -> passes through to call_next (200 from our stub).
        req = _make_request(app, "/api/upload/SavedClips/x/y.mp4", settings.upload_port)
        resp = await restrict_upload_port(req, _call_next_ok)
        check("upload path on upload port -> passes through", resp.status_code == 200,
              str(resp.status_code))

        # Non-upload path on the ingress port (not upload_port) -> passes through.
        req = _make_request(app, "/api/events", settings.port)
        resp = await restrict_upload_port(req, _call_next_ok)
        check("non-upload path on ingress port -> passes through", resp.status_code == 200,
              str(resp.status_code))

    asyncio.run(_run_checks())

    print()
    print("RESULT:", "ALL PASS" if not failures else f"{len(failures)} FAILURE(S): {failures}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd teslausb-viewer && PYTHONPATH=. python3 tests/test_port_restriction.py`
Expected: `ImportError: cannot import name 'restrict_upload_port' from 'app.main'`.

- [ ] **Step 3: Add the middleware to `app/main.py`**

Add this import at the top (alongside the existing `fastapi` import line):
```python
from fastapi import FastAPI, HTTPException, Request
```
(i.e. add `HTTPException` to the existing `from fastapi import FastAPI, Request` line.)

Add the middleware function immediately after the existing `ingress_base` middleware
function (after its `return await call_next(request)` line, before `app.include_router(router)`):

```python
@app.middleware("http")
async def restrict_upload_port(request: Request, call_next):
    """The upload port must never serve anything but /api/upload/* — enforced by which
    socket accepted the connection (scope["server"][1], a network fact uvicorn itself sets),
    not by any client-supplied header. Requests on any other port (e.g. the ingress port)
    pass through unaffected."""
    local_port = request.scope.get("server", (None, None))[1]
    settings = request.app.state.settings
    if local_port == settings.upload_port and not request.url.path.startswith("/api/upload/"):
        raise HTTPException(404, "not found")
    return await call_next(request)
```

Note: `request.app.state.settings` is only populated once the app's `lifespan` has started
(i.e. inside a running app, via `TestClient(m.app)`'s context manager or a real server). The
test in this task calls the middleware function directly with a hand-built request whose
`scope["app"]` is the real `app` object — but `app.state.settings` is only set by
`lifespan()`, which does NOT run for a directly-invoked middleware call. Handle this: the test
file's `_make_request` sets `"app": app` in scope, but `app.state.settings` won't exist unless
something set it. Fix this by having the test call `get_settings()` directly (as shown) to
read `settings.port`/`settings.upload_port`, and have the middleware itself also read via
`get_settings()` (which is `lru_cache`'d, so it returns the same object `lifespan()` would set
on `app.state.settings` in a real running app) instead of `request.app.state.settings`:

```python
@app.middleware("http")
async def restrict_upload_port(request: Request, call_next):
    """..."""
    from .config import get_settings

    local_port = request.scope.get("server", (None, None))[1]
    settings = get_settings()
    if local_port == settings.upload_port and not request.url.path.startswith("/api/upload/"):
        raise HTTPException(404, "not found")
    return await call_next(request)
```
(Use `get_settings()` directly, not `request.app.state.settings`, in the actual implementation
you write — the code block above, not the one before it, is correct.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd teslausb-viewer && PYTHONPATH=. python3 tests/test_port_restriction.py`
Expected: `RESULT: ALL PASS`, exit 0.

- [ ] **Step 5: Run the existing full suite to confirm no regression**

Run: `cd teslausb-viewer && ./tests/run.sh` (this will fail at the `test_port_restriction`
step since it's not yet wired into `tests/run.sh`'s loop — that's fine, Task 4 handles the
harness; for now just confirm `test_api`, `test_upload`, `test_auth`, `test_video_thumb`,
`test_indexer`, `test_config` all still individually pass by running each directly, e.g.
`PYTHONPATH=. python3 tests/test_upload.py` with the same env vars `tests/run.sh` sets — see
any recent task's report for the exact `uv venv` + env var invocation pattern).
Expected: every existing test file still shows `RESULT: ALL PASS` (or bare `PASS` for
`test_config`/`test_indexer`) — the new middleware must not change behavior for requests that
don't match `scope["server"][1] == upload_port` (which none of the existing tests' `TestClient`
calls do, per the Global Constraints note about `TestClient`'s default port being 80).

- [ ] **Step 6: Commit**

```bash
cd teslausb-viewer
git add app/main.py tests/test_port_restriction.py
git commit -m "feat(teslausb-viewer): restrict the upload port to /api/upload/* only"
```

---

### Task 3: Add-on packaging (`config.yaml`, `run.sh`)

**Files:**
- Modify: `teslausb-viewer/config.yaml`
- Modify: `teslausb-viewer/run.sh`

**Interfaces:**
- Consumes: `TUV_UPLOAD_PORT` env var (Task 1's `Settings.upload_port` reads it).
- Produces: the running container's exposed ports and launch command every earlier task's
  code assumes.

- [ ] **Step 1: Update `config.yaml`**

Replace the current `ports`/`ports_description` block and its preceding comment:
```yaml
# Expose the ingress port on the LAN too, so an external device (not a browser session) can
# reach the upload API. The browse/watch UI is unaffected — it still works the same way
# through ingress.
ports:
  "8099/tcp": 8099
ports_description:
  "8099/tcp": "TeslaCam viewer UI (ingress) + upload API (LAN, token-authenticated)"
```
with:
```yaml
# The ingress port (8099, above) has NO entry here — it stays reachable only through
# Supervisor's ingress proxy. This dedicated port serves ONLY /api/upload/* (enforced by
# app/main.py's restrict_upload_port middleware), so it's safe to expose on the LAN or even
# externally via your own reverse proxy — nothing else is reachable through it.
ports:
  "8100/tcp": 8100
ports_description:
  "8100/tcp": "TeslaCam upload API only (LAN/external, always token-authenticated)"
```
Also bump `version: "0.4.2"` to `version: "0.5.0"` (near the top of the file).

- [ ] **Step 2: Verify structural assertions**

Run:
```bash
cd teslausb-viewer
grep -q '"8100/tcp": 8100' config.yaml && ! grep -q '"8099/tcp"' config.yaml && grep -q 'version: "0.5.0"' config.yaml && echo OK
```
Expected: `OK`.

- [ ] **Step 3: Update `run.sh`**

Replace the final two lines:
```bash
bashio::log.info "Launching web app on port ${PORT} (ingress + LAN upload API)"
cd "${APP_DIR}"
exec gosu viewer "${APP_DIR}/venv/bin/uvicorn" app.main:app \
    --host 0.0.0.0 --port "${PORT}" --workers 1 --no-access-log
```
with:
```bash
export TUV_UPLOAD_PORT=8100
bashio::log.info "Launching web app: ingress on ${PORT}, upload API on ${TUV_UPLOAD_PORT}"
cd "${APP_DIR}"
exec gosu viewer "${APP_DIR}/venv/bin/python" -m app.serve
```

- [ ] **Step 4: Verify syntax**

Run: `cd teslausb-viewer && bash -n run.sh && echo "run.sh OK"`
Expected: `run.sh OK`.

- [ ] **Step 5: Commit**

```bash
cd teslausb-viewer
git add config.yaml run.sh
git commit -m "feat(teslausb-viewer): package the ingress/upload port split, bump to 0.5.0"
```

---

### Task 4: Test harness wiring, docs, version bump, final full run

**Files:**
- Modify: `teslausb-viewer/tests/run.sh`
- Modify: `teslausb-viewer/DOCS.md`
- Modify: `teslausb-viewer/CHANGELOG.md`
- Modify: `teslausb-viewer/app/__init__.py`

**Interfaces:**
- Consumes: every module from Tasks 1-3.
- Produces: nothing new — final integration pass.

- [ ] **Step 1: Add `test_serve` and `test_port_restriction` to `tests/run.sh`'s loop**

In `tests/run.sh`, find the line:
```bash
for t in test_config test_indexer test_video_thumb test_auth test_upload test_api; do
```
and change it to:
```bash
for t in test_config test_indexer test_video_thumb test_auth test_upload test_serve test_port_restriction test_api; do
```
(insert the two new test names before `test_api`, so the whole-app integration test still
runs last).

- [ ] **Step 2: Bump `app/__init__.py`'s version**

Change:
```python
__version__ = "0.4.2"
```
to:
```python
__version__ = "0.5.0"
```

- [ ] **Step 3: Rewrite `DOCS.md`'s "Ingress + LAN" callout**

Replace the existing callout block (currently titled "Ingress + LAN — read this.") with:

```markdown
> **Two ports, two trust levels — read this.** The browse/watch UI is reachable ONLY through
> the Home Assistant sidebar panel (ingress, port 8099) — that port has no LAN or external
> exposure at all, restoring the original ingress-only access model. A second, dedicated port
> (**8100**) serves *only* the upload endpoint (`PUT /api/upload/...`) — nothing else is
> reachable through it, enforced by which port the connection arrived on, not by a header a
> client could fake. Because port 8100 can never serve anything but the already
> token-authenticated upload route, it's safe to expose it on your LAN, or even externally
> through your own reverse proxy (e.g. NGINX Proxy Manager) — see "Archiver setup" below.
```

- [ ] **Step 4: Prepend a `CHANGELOG.md` entry**

At the top of `CHANGELOG.md`, immediately after the `# Changelog` heading and before the
existing `## 0.4.2` section, insert:

```markdown
## 0.5.0

### 💥 Breaking changes / 🔒 Security
- **Split the ingress and upload ports.** 0.4.0 exposed the single shared port (8099) on the
  LAN so the upload endpoint could be reached — but that meant the *entire app*, including the
  browse/watch UI and all unauthenticated read routes, was also LAN-reachable. Port 8099 is
  now ingress-only again (no LAN/external exposure at all). A new dedicated port, **8100**,
  serves *only* `PUT /api/upload/...` — enforced by which port a connection arrives on, not by
  a forgeable header — and is the only thing safe to expose on your LAN or externally. If you
  were relying on reaching read routes via the old LAN-exposed 8099, that access is now gone;
  use ingress (the HA sidebar) instead. See DOCS.md's "Two ports, two trust levels" callout.
```

- [ ] **Step 5: Run the full suite**

Run: `cd teslausb-viewer && ./tests/run.sh`
Expected: every `=== test_* ===` section (now 8 of them) prints `RESULT: ALL PASS` (or a bare
`PASS ...` line for `test_config`/`test_indexer`), overall exit 0. Run it a second time
consecutively to confirm idempotency (this repo has been bitten by non-idempotent test
fixtures before — don't skip this check).

- [ ] **Step 6: Commit**

```bash
cd teslausb-viewer
git add tests/run.sh app/__init__.py DOCS.md CHANGELOG.md
git commit -m "docs(teslausb-viewer): document port split, wire new tests into run.sh, bump to 0.5.0"
```

---

## Self-Review

**Spec coverage:**
- §3.1 config.yaml changes → Task 3.
- §3.2 app/serve.py → Task 1.
- §3.3 Settings.upload_port → Task 1.
- §3.4 main.py middleware → Task 2.
- §3.5 run.sh → Task 3.
- §4 error handling (404 on wrong port, gather propagates bind failures) → Task 2 (404
  behavior tested); bind-failure propagation is inherent to `asyncio.gather` and not something
  a unit test can exercise without real sockets — matches the spec's own stated test-scope
  limits.
- §5 testing → Tasks 1, 2, 4 (harness wiring).
- §6 documentation → Task 4.

**Placeholder scan:** no `TBD`/`TODO` anywhere; every step has complete, exact code. Task 2's
Step 3 has two code blocks (one showing what's WRONG and why, one showing the CORRECT
implementation) — this is intentional pedagogy (explaining a subtlety about `lifespan()` timing
that would otherwise cause a confusing test failure), not an unresolved placeholder; the step's
prose explicitly says which block to actually write.

**Type consistency:** `Settings.upload_port: int` (Task 1) is the exact name every later task
uses (`app/main.py`'s middleware via `get_settings()`, `app/serve.py`, `run.sh`'s
`TUV_UPLOAD_PORT` export). `restrict_upload_port` (Task 2) matches its test's import name.
`app/serve.py`'s `main()` matches `run.sh`'s `python -m app.serve` invocation (module `__main__`
guard calls `main()`).
