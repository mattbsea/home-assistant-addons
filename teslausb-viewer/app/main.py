"""FastAPI application: wiring, ingress base-path handling, and the background scanner."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from . import stats, thumbnailer
from .api import router
from .cache import CacheManager
from .config import get_settings
from .db import Database
from .indexer import Indexer
from .mqtt_publisher import MqttPublisher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("teslausb_viewer")

WEB_DIR = Path(__file__).parent / "web"


async def refresh_and_publish(app: FastAPI) -> dict:
    """Run one index scan, generate missing thumbnails, then push stats. The single refresh path."""
    result = await app.state.indexer.scan()
    try:
        await thumbnailer.backfill(app.state.settings, app.state.db, app.state.cache)
    except Exception:  # noqa: BLE001 — thumbnail generation must never break a scan
        log.exception("Thumbnail backfill failed")
    try:
        values = await stats.compute(app.state.settings, app.state.db)
        app.state.mqtt.publish_states(values)
    except Exception:  # noqa: BLE001 — stats/MQTT must never break a scan
        log.exception("Failed to compute/publish stats")
    return result


async def _scan_loop(app: FastAPI) -> None:
    interval = app.state.settings.refresh_minutes * 60
    # Initial scan shortly after startup so the UI populates without waiting a full interval.
    await asyncio.sleep(2)
    while True:
        try:
            await refresh_and_publish(app)
        except Exception:  # noqa: BLE001
            log.exception("Scan loop iteration failed")
        await asyncio.sleep(interval)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.db = Database(settings.db_path)
    app.state.indexer = Indexer(settings, app.state.db)
    app.state.cache = CacheManager(settings)
    app.state.mqtt = MqttPublisher(settings)
    app.state.mqtt.start()
    app.state.scan_task = asyncio.create_task(_scan_loop(app))
    log.info("TeslaUSB Viewer ready (backend configured: %s)", settings.has_backend())
    try:
        yield
    finally:
        app.state.scan_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await app.state.scan_task
        app.state.mqtt.stop()
        app.state.db.close()


app = FastAPI(title="TeslaUSB Viewer", lifespan=lifespan)


# A valid ingress prefix is a plain URL path. Restricting to this charset means the value
# is safe to reflect into both HTML attributes and the JS string in index.html — no quotes,
# angle brackets or "</script>" can survive, closing the X-Ingress-Path injection vector.
_INGRESS_PATH_RE = re.compile(r"/[A-Za-z0-9_\-/]*")


@app.middleware("http")
async def ingress_base(request: Request, call_next):
    """Expose a validated HA ingress path prefix so the frontend can build absolute URLs."""
    raw = request.headers.get("X-Ingress-Path", "")
    request.state.ingress_base = raw.rstrip("/") if _INGRESS_PATH_RE.fullmatch(raw) else ""
    return await call_next(request)


app.include_router(router)
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
