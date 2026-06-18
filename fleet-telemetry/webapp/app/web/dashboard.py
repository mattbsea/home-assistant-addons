"""Dashboard web app: serves the static dashboard page and the /api/state data API from the Store.

The dashboard UI (HTML/CSS/JS) lives as a static asset (extracted from the v0 inline page); the app
just serves it and feeds it /api/state. Runs on the ingress port.
"""
import os

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from app.web import api

_STATIC = os.path.join(os.path.dirname(__file__), "static")


def _dashboard_html():
    with open(os.path.join(_STATIC, "dashboard.html")) as fh:
        return fh.read()


def build_dashboard_app(store, *, version="", cert_getter=None, namespace=""):
    html = _dashboard_html()

    async def index(_req):
        return HTMLResponse(html)

    async def state(_req):
        cert = cert_getter() if cert_getter else {}
        return JSONResponse(api.state_payload(store, version=version, cert=cert, namespace=namespace))

    return Starlette(routes=[
        Route("/", index),
        Route("/api/state", state),
    ])
