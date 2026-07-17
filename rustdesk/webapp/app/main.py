#!/usr/bin/env python3
"""RustDesk add-on ingress dashboard.

Read-only status/connection panel for the hbbs/hbbr processes `run.sh` supervises. Deliberately NOT
an in-browser remote-desktop viewer: RustDesk's actual remote-desktop protocol needs raw TCP/UDP
that Home Assistant's ingress (a single HTTP+WebSocket proxy) cannot carry. See DOCS.md.

Reads two things `run.sh` maintains, never writes to either:
  - STATE_PATH: a small JSON file with the current relay/key config and detected LAN IP
  - LOG_DIR: hbbs.log / hbbr.log, size-capped by run.sh
Process liveness is checked directly from PID files run.sh writes on each (re)start.
"""
import json
import os

import uvicorn
from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Route

STATE_PATH = os.environ.get("RD_STATE_FILE", "/data/state.json")
RUN_DIR = os.environ.get("RD_RUN_DIR", "/data/run")
LOG_DIR = os.environ.get("RD_LOG_DIR", "/data/logs")
KEY_DIR = os.environ.get("RD_KEY_DIR", "/data/rustdesk")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

LOG_TAIL_BYTES = 32 * 1024
PROCS = ("hbbs", "hbbr")


def _load_state():
    try:
        with open(STATE_PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _proc_alive(name):
    """A PID file plus a live /proc entry whose cmdline mentions the binary name.

    The cmdline check guards against a stale PID file pointing at a since-recycled PID that now
    belongs to an unrelated process — cheap enough to be worth doing for a status indicator.
    """
    pid_path = os.path.join(RUN_DIR, f"{name}.pid")
    try:
        with open(pid_path) as fh:
            pid = int(fh.read().strip())
    except (OSError, ValueError):
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            cmdline = fh.read().decode("utf-8", "replace")
    except OSError:
        return False
    return name in cmdline


def _read_pubkey():
    try:
        with open(os.path.join(KEY_DIR, "id_ed25519.pub")) as fh:
            return fh.read().strip()
    except OSError:
        return None


def _tail(path, max_bytes):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()  # drop a possibly-truncated first line
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


async def api_status(_request):
    state = _load_state()
    return JSONResponse({
        "version": os.environ.get("RD_ADDON_VERSION", ""),
        "public_key": _read_pubkey(),
        "relay_host": state.get("relay_host", ""),
        "encrypted_only": state.get("encrypted_only", True),
        "custom_key_set": state.get("custom_key_set", False),
        "local_ip": state.get("local_ip", ""),
        "procs": {name: _proc_alive(name) for name in PROCS},
        "ports": {
            "21115/tcp": "NAT type test (hbbs)",
            "21116/tcp": "Hole punching (hbbs)",
            "21116/udp": "ID registration & heartbeat (hbbs)",
            "21117/tcp": "Relay service (hbbr)",
            "21118/tcp": "Web client WebSocket (hbbs)",
            "21119/tcp": "Web client WebSocket (hbbr)",
        },
    })


async def api_logs(request):
    name = request.path_params["name"]
    if name not in PROCS:
        return PlainTextResponse("unknown process", status_code=404)
    return PlainTextResponse(_tail(os.path.join(LOG_DIR, f"{name}.log"), LOG_TAIL_BYTES))


async def index(_request):
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app = Starlette(routes=[
    Route("/", index),
    Route("/api/status", api_status),
    Route("/api/logs/{name}", api_logs),
])


def main():
    port = int(os.environ.get("RD_WEB_PORT", "8092"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
