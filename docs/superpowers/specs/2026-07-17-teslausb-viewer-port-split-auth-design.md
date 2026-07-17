# TeslaUSB Viewer — Port-Split Auth Hardening

- **Date:** 2026-07-17
- **Status:** Approved design, pending implementation plan
- **Component:** `teslausb-viewer` add-on
- **Target version:** 0.5.0 (network-topology change)
- **Related:** [2026-07-16 local-upload-api design](2026-07-16-teslausb-viewer-local-upload-api-design.md),
  which first exposed port 8099 on the LAN. This spec corrects that exposure ahead of the
  user wanting to reach the upload endpoint from outside the LAN (via a reverse proxy).

## 1. Problem & Context

The 0.4.0 design exposed the add-on's single ingress port (8099) on the LAN too, reasoning
that only the new `/api/upload/*` route needed reaching from outside a browser session, and
that route is bearer-token gated. In practice this means **every route** is LAN-reachable —
the browse/watch UI, `GET /api/events`, video streaming, `POST /api/refresh` — with **no
authentication at all**, since only the upload route checks a token. This was flagged (and
knowingly accepted for LAN-only exposure) in the 0.4.0 review.

The user now wants to reach the upload endpoint from **outside** their LAN, via their own
reverse proxy (NGINX Proxy Manager) fronting a public domain. Proxying the same port
externally would make the *entire unauthenticated app* — including dashcam footage — reachable
from the public internet. That is not an acceptable trade for enabling remote uploads.

**Goal:** make the upload endpoint the *only* thing that can ever be exposed beyond the LAN
(or even beyond ingress), by construction — not by a header check that a direct client could
forge.

### Goals

- Restore the ingress port (8099) to ingress-only: no host `ports:` mapping, reachable only
  through Supervisor's ingress proxy (an authenticated Home Assistant session).
- Add a second, dedicated port (8100) that serves **only** `/api/upload/*` — nothing else is
  reachable through it, enforced by which socket accepted the connection (a network fact),
  not by an inspectable/forgeable header.
- This second port is then safe for the user to proxy externally via their own reverse proxy,
  since it can never serve anything but the already-token-gated upload route.

### Non-Goals

- Adding auth to the read routes themselves (browse/watch UI, `/api/events`, video, `/refresh`)
  — out of scope; they remain ingress-only, which was already their access model before 0.4.0.
- Any change to the upload route's own validation, atomicity, or auth logic (from 0.4.0/0.4.2)
  — unchanged, just now reachable exclusively through the new dedicated port.
- Configuring the user's own external reverse proxy (NPM) — out of scope for this add-on; the
  user does that themselves once port 8100 is the only thing worth exposing.

## 2. Approach

Run **two independent `uvicorn.Server` instances in one process**, both serving the same
FastAPI `app` object, on two different ports:
- **8099** (`ingress_port` in `config.yaml`, unchanged) — no host `ports:` mapping, so it's
  reachable only via Supervisor's internal ingress proxy. Serves the full route table
  (UI + all API routes, including `/api/upload/*` — harmless, since ingress traffic is an
  authenticated browser session anyway and nothing about serving upload there weakens
  anything).
- **8100** (new) — host `ports:` mapped (LAN, and whatever the user chooses to proxy
  externally). A middleware inspects which local port accepted the connection
  (`request.scope["server"]`, the ASGI server address/port tuple — not a client-supplied
  header) and returns `404` for any request on port 8100 whose path doesn't start with
  `/api/upload/`.

`run.sh` switches from a single `uvicorn app.main:app --port $PORT` CLI invocation to a small
launcher module, `app/serve.py`, that builds two `uvicorn.Config`s (same `app`, different
`port`) and runs both `Server.serve()` coroutines concurrently via `asyncio.gather` in one
process — one process, one set of in-memory state (DB connection, scan loop, MQTT), just two
listening sockets.

**Rejected alternatives:**
- *Trust `X-Ingress-Path` (or similar header) to distinguish ingress vs. LAN traffic on a
  single shared port* — this is exactly the arrangement the current 0.4.0 code has, and the
  header is entirely client-supplied on a directly-reachable port; any direct client can set
  it and there is nothing to stop them. This is the vulnerability being fixed, not a valid
  alternative to it.
- *Trust source IP (Supervisor's internal ingress-proxy address) to distinguish traffic* — no
  stable, documented internal IP to check; more fragile than a real network-topology fact, and
  security-by-obscurity if it did work.
- *Two separate FastAPI apps / two separate `main.py` entrypoints* — unnecessary process
  duplication (two DB connections, two scan loops, two MQTT clients all fighting over the same
  `teslacam_path`/`index.db`); the two-listeners-one-app approach shares all of that safely
  since it's still one Python process, one event loop.

## 3. Architecture

### 3.1 `config.yaml` changes

```yaml
ingress: true
ingress_port: 8099        # unchanged — ingress-only now, no ports: entry for it
ingress_stream: true

ports:
  "8100/tcp": 8100         # NEW — replaces the old 8099 mapping
ports_description:
  "8100/tcp": "TeslaCam upload API (LAN/external, token-authenticated only)"
```
`homeassistant_api: true`/`hassio_api: true` unchanged. Bump `version` to `0.5.0`.

### 3.2 `app/serve.py` (new)

```python
"""Runs two uvicorn listeners against the same FastAPI app: the ingress port (full route
table, reachable only via Supervisor's ingress proxy) and the upload port (restricted by
main.py's middleware to /api/upload/* only, reachable on the LAN/externally)."""

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

### 3.3 `app/config.py`: new `upload_port` field

`Settings` gains `upload_port: int`, read from `TUV_UPLOAD_PORT` (default `8100`), parallel
to the existing `port` field (default `8099`, unchanged meaning: the ingress port).

### 3.4 `app/main.py`: port-restriction middleware

```python
@app.middleware("http")
async def restrict_upload_port(request: Request, call_next):
    """The upload port must never serve anything but /api/upload/* — enforced by which
    socket accepted the connection (scope["server"][1], a network fact set by uvicorn
    itself), not by any client-supplied header."""
    local_port = request.scope.get("server", (None, None))[1]
    settings = request.app.state.settings
    if local_port == settings.upload_port and not request.url.path.startswith("/api/upload/"):
        raise HTTPException(404, "not found")
    return await call_next(request)
```
Registered alongside the existing `ingress_base` middleware in `main.py` (order doesn't
matter between these two — neither depends on the other's `request.state`).

### 3.5 `run.sh`

Replace the final `exec gosu viewer .../uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" ...`
line with:
```bash
export TUV_UPLOAD_PORT=8100
bashio::log.info "Launching web app: ingress on ${PORT}, upload API on ${TUV_UPLOAD_PORT}"
cd "${APP_DIR}"
exec gosu viewer "${APP_DIR}/venv/bin/python" -m app.serve
```
(`uvicorn` is still a dependency, just invoked programmatically instead of via its CLI.)

## 4. Error Handling

- A non-upload path on port 8100 → `404` (matches the existing "not found" semantics for any
  unmapped route — doesn't leak "this route exists but you're on the wrong port").
- If `uvicorn.Server.serve()` on either listener fails to bind (port already in use, unlikely
  but possible on a re-deploy race), `asyncio.gather` propagates the exception and the process
  exits non-zero — Supervisor's normal restart-on-crash behavior applies, same as today.

## 5. Testing

- New `tests/test_port_restriction.py`: builds two `TestClient`-equivalent requests against
  the same `app` but with different `scope["server"]` ports (FastAPI's `TestClient` allows
  overriding scope via `ASGITransport`/`app.router` direct scope construction, or simpler:
  call the middleware function directly with a hand-built `Request` for each port value) —
  assert a non-upload path 404s on the upload port and 200s on the ingress port, and the
  upload path itself works on both.
- Existing `tests/test_upload.py` continues to exercise the upload route directly via
  `TestClient(app)` (which doesn't go through either real uvicorn listener) — unaffected,
  still valid since the middleware only restricts based on `scope["server"]`, and `TestClient`
  defaults that to a fixed testserver port that isn't `settings.upload_port`, so those
  requests behave like ingress-port requests (full route table) unless a test explicitly
  overrides `scope["server"]` to match `upload_port`.
- No test spins up a real uvicorn socket pair (out of scope/flaky for this suite) — the
  dual-listener wiring in `app/serve.py` is verified by a `bash -n`-equivalent Python import
  smoke test, and functionally by the user's own live re-test after deploy (same pattern used
  for 0.4.2's Supervisor-proxy finding — the harness can't spin up a real Supervisor either).

## 6. Documentation

`DOCS.md`'s "Ingress + LAN" callout gets rewritten to describe the new topology: ingress-only
UI (8099, no LAN/external exposure), dedicated upload port (8100) safe to expose externally
via the user's own reverse proxy since it can only ever serve `/api/upload/*`. `CHANGELOG.md`
gets a `0.5.0` entry documenting this as a security-hardening breaking change (anyone who was
relying on reaching read routes via the old LAN-exposed 8099 loses that — intentional).
