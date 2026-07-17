"""HTTP API and the ingress-aware index page.

Every URL the browser uses is built in the frontend from window.INGRESS_BASE, so these
handlers use plain relative paths. The one exception is `/` which injects that base into
index.html. /video serves the clip directly off local disk with Range support (see
app/rangeserve.py).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from . import __version__, stats
from .cache import order_cameras
from .models import FOLDERS
from .rangeserve import serve_file_range

router = APIRouter()
WEB_DIR = Path(__file__).parent / "web"


def _state(request: Request):
    return request.app.state


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    base = getattr(request.state, "ingress_base", "")
    html = html.replace("{{INGRESS_BASE}}", base).replace("{{VERSION}}", __version__)
    # Always revalidate the shell so a deploy's new asset references are picked up.
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@router.get("/api/health")
async def health(request: Request) -> JSONResponse:
    st = _state(request)
    last = await asyncio.to_thread(st.db.get_meta, "last_index_refresh")
    return JSONResponse({
        "ok": True,
        "backend_configured": st.settings.has_backend(),
        "teslacam_path": str(st.settings.teslacam_path) if st.settings.has_backend() else None,
        "last_refresh": last,
        "mqtt_connected": st.mqtt.connected,
    })


@router.get("/api/stats")
async def get_stats(request: Request) -> JSONResponse:
    st = _state(request)
    return JSONResponse(await stats.compute(st.settings, st.db))


@router.get("/api/events")
async def list_events(
    request: Request,
    folder: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    limit: int = Query(default=60, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> JSONResponse:
    if folder and folder not in FOLDERS:
        raise HTTPException(400, f"unknown folder {folder!r}")
    st = _state(request)
    rows, total = await asyncio.to_thread(
        st.db.list_events, folder=folder, date_from=date_from,
        date_to=date_to, limit=limit, offset=offset,
    )
    events = [
        {
            "event_id": r["event_id"],
            "folder": r["folder"],
            "event_ts": r["event_ts"],
            "reason": r["reason"],
            "city": r["city"],
            "est_lat": r["est_lat"],
            "est_lon": r["est_lon"],
            "thumb_present": bool(r["thumb_present"]),
            "file_count": r["file_count"],
            "minute_count": r["minute_count"],
        }
        for r in rows
    ]
    return JSONResponse({"events": events, "total": total, "limit": limit, "offset": offset})


@router.get("/api/events/{event_id:path}/detail")
async def event_detail(event_id: str, request: Request) -> JSONResponse:
    st = _state(request)
    row = await asyncio.to_thread(st.db.get_event, event_id)
    if not row:
        raise HTTPException(404, "event not found")
    event = st.db.to_event(row)
    minutes = [
        {"minute_ts": m, "cameras": event.cameras_for_minute(m)}
        for m in event.minutes
    ]
    cameras = order_cameras(sorted({f.camera for f in event.files}))
    return JSONResponse({
        "event_id": event.event_id,
        "folder": event.folder,
        "event_ts": event.event_ts,
        "reason": event.reason,
        "city": event.city,
        "est_lat": event.est_lat,
        "est_lon": event.est_lon,
        "thumb_present": event.thumb_present,
        "cameras": cameras,
        "minutes": minutes,
    })


@router.get("/api/events/{event_id:path}/thumb")
async def thumb(event_id: str, request: Request) -> Response:
    data = await _state(request).cache.get_thumb(event_id)
    if not data:
        raise HTTPException(404, "no thumbnail")
    return Response(content=data, media_type="image/png",
                    headers={"Cache-Control": "max-age=86400"})


@router.get("/api/events/{event_id:path}/video/{camera}/{minute_ts}")
async def video(event_id: str, camera: str, minute_ts: str, request: Request) -> Response:
    st = _state(request)
    row = await asyncio.to_thread(st.db.find_file, event_id, camera, minute_ts)
    if not row:
        raise HTTPException(404, "no such clip")
    # `path` is the clip's location under teslacam_path (recorded since 0.1.7); older rows
    # fall back to the folder-shaped path the copy model assumed.
    rel_path = row.get("path") or f"{event_id}/{row['filename']}"
    local_path = (st.settings.teslacam_path / rel_path).resolve()
    root = st.settings.teslacam_path.resolve()
    if not local_path.is_relative_to(root):
        raise HTTPException(404, "no such clip")
    return serve_file_range(local_path, request.headers.get("range"))


@router.post("/api/refresh")
async def refresh(request: Request) -> JSONResponse:
    from .main import refresh_and_publish

    result = await refresh_and_publish(request.app)
    return JSONResponse(result)
