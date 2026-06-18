"""Wizard web app: serves the setup page and the config/keypair control-plane endpoints.

Backed by the ported control modules (config, keys). The Tesla OAuth / partner-registration /
NPM-provisioning / send-config routes are added on top of this same app as those flows are wired.
"""
import json
import os

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from app.control import config as cfgmod
from app.control import keys

_STATIC = os.path.join(os.path.dirname(__file__), "static")


async def _json_body(req):
    raw = await req.body()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return None


def build_wizard_app(*, config_path, private_key_path, public_key_path):
    with open(os.path.join(_STATIC, "wizard.html")) as fh:
        page = fh.read()

    async def index(_req):
        return HTMLResponse(page)

    async def get_config(_req):
        return JSONResponse(cfgmod.redacted(config_path))

    async def post_config(req):
        patch = await _json_body(req)
        if not isinstance(patch, dict):
            return JSONResponse({"error": "config patch must be a JSON object"}, status_code=400)
        cfgmod.save(config_path, cfgmod.strip_secret_masks(patch))
        return JSONResponse(cfgmod.redacted(config_path))

    async def post_keypair(req):
        body = await _json_body(req)
        force = bool(body.get("force")) if isinstance(body, dict) else False
        r = keys.generate_keypair(private_key_path, public_key_path, force=force)
        if r.get("ok"):
            cfgmod.save(config_path, {"tesla": {"keypair_generated": True}})
        return JSONResponse(r)

    return Starlette(routes=[
        Route("/", index),
        Route("/api/config", get_config, methods=["GET"]),
        Route("/api/config", post_config, methods=["POST"]),
        Route("/api/keypair", post_keypair, methods=["POST"]),
    ])
