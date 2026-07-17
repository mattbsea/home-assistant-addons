"""Runs two uvicorn listeners against the same FastAPI app: the ingress port (full route
table, reachable only via Supervisor's ingress proxy) and the upload port (restricted by
main.py's `restrict_upload_port` middleware to /api/upload/* only, reachable on the
LAN/externally). One process, one event loop, one DB connection/scan loop/MQTT client —
just two listening sockets.

Each uvicorn.Server independently drives the ASGI lifespan protocol against whatever app
it's given — it has no idea the other Server was handed the SAME app object. Left to their
own devices, both Servers would call app's lifespan() once each, opening two DB
connections/scan loops/MQTT clients and clobbering `app.state` (a single shared dict)
between them. So lifespan handling is disabled on both uvicorn.Config objects
(``lifespan="off"``) and the app's lifespan is instead driven exactly once, here, wrapping
both Server.serve() calls — it enters before either server starts accepting connections,
so the upload listener's route handlers (which read request.app.state.settings) never race
against startup.
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
        lifespan="off",
    ))
    upload = uvicorn.Server(uvicorn.Config(
        app, host="0.0.0.0", port=settings.upload_port, workers=1, access_log=False,
        lifespan="off",
    ))
    async with app.router.lifespan_context(app):
        await asyncio.gather(ingress.serve(), upload.serve())


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
