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
