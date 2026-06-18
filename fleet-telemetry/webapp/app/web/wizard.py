"""Wizard web app: serves the setup page and the control-plane endpoints, wired onto the ported
control modules (config, keys, tesla, npm, sendconfig). The route layer reads/persists config; the
modules do the external work.
"""
import json
import os

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from app.control import config as cfgmod
from app.control import keys, npm, sendconfig, tesla
from app.web import api

_STATIC = os.path.join(os.path.dirname(__file__), "static")


def _configured(c):
    """The setup is complete enough to show the dashboard instead of the wizard."""
    return bool(c["npm"].get("url") and c["npm"].get("cert_domain") and c["tesla"].get("client_id"))


async def _json_body(req):
    raw = await req.body()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return None


def build_wizard_app(*, config_path, private_key_path, public_key_path,
                     cert_file=None, certs_dir=None, registry=None,
                     store=None, version="", namespace="", cert_getter=None):
    with open(os.path.join(_STATIC, "wizard.html")) as fh:
        wizard_page = fh.read()
    dash_page = None
    if store is not None:
        with open(os.path.join(_STATIC, "dashboard.html")) as fh:
            dash_page = fh.read()

    def cfg():
        return cfgmod.load(config_path)

    async def index(_req):
        # Show the dashboard once configured; otherwise the setup wizard.
        if store is not None and _configured(cfg()):
            return HTMLResponse(dash_page)
        return HTMLResponse(wizard_page)

    async def state(_req):
        cert = cert_getter() if cert_getter else {}
        return JSONResponse(api.state_payload(store, version=version, cert=cert, namespace=namespace))

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

    async def oauth_url(req):
        c = cfg()["tesla"]
        url = tesla.authorize_url(c.get("client_id", ""), req.query_params.get("redirect_uri", ""),
                                  req.query_params.get("state", ""))
        return JSONResponse({"url": url})

    async def oauth_exchange(req):
        body = await _json_body(req) or {}
        c = cfg()["tesla"]
        r = tesla.exchange_code(client_id=c.get("client_id", ""), client_secret=c.get("client_secret", ""),
                                code=body.get("code", ""), redirect_uri=body.get("redirect_uri", ""),
                                region=c.get("region", "na"))
        if r.get("ok"):
            cfgmod.save(config_path, {"tesla": {"shim_refresh_token": r["refresh_token"]}})
            return JSONResponse({"ok": True})
        return JSONResponse(r)

    async def partner(_req):
        c = cfg()["tesla"]
        r = tesla.register_partner(client_id=c.get("client_id", ""), client_secret=c.get("client_secret", ""),
                                   domain=c.get("pubkey_domain", ""), region=c.get("region", "na"))
        if r.get("ok"):
            cfgmod.save(config_path, {"tesla": {"partner_registered": True}})
        return JSONResponse(r)

    async def npm_cert(_req):
        n = cfg()["npm"]
        r = npm.fetch_cert(n.get("url", ""), n.get("email", ""), n.get("password", ""),
                           n.get("cert_domain", ""), certs_dir or ".")
        return JSONResponse(r)

    async def npm_pubkey_host(_req):
        c, n = cfg()["tesla"], cfg()["npm"]
        r = npm.create_pubkey_host(base=n.get("url", ""), email=n.get("email", ""), password=n.get("password", ""),
                                   domain=c.get("pubkey_domain", ""), forward_host=n.get("forward_host", ""),
                                   forward_port=int(os.environ.get("FT_PUBKEY_PORT", "8100")))
        if r.get("ok"):
            cfgmod.save(config_path, {"npm": {"pubkey_proxy_host_id": r["id"]}})
        return JSONResponse(r)

    async def npm_stream(_req):
        c, n = cfg()["tesla"], cfg()["npm"]
        try:
            port = int(c.get("telemetry_port") or 4443)
        except (TypeError, ValueError):
            port = 4443
        r = npm.create_stream(base=n.get("url", ""), email=n.get("email", ""), password=n.get("password", ""),
                              incoming_port=port, forward_host=n.get("forward_host", ""),
                              forward_port=int(os.environ.get("FT_TELEMETRY_HOST_PORT", "4443")))
        if r.get("ok"):
            cfgmod.save(config_path, {"npm": {"stream_id": r["id"]}})
        return JSONResponse(r)

    async def send_config(_req):
        c = cfg()["tesla"]
        vins = registry.vins() if registry else []
        try:
            port = int(c.get("telemetry_port") or 4443)
        except (TypeError, ValueError):
            port = 4443
        r = sendconfig.send(vins=vins, client_id=c.get("client_id", ""),
                            refresh_token=c.get("shim_refresh_token", ""),
                            domain=c.get("telemetry_domain", ""), region=c.get("region", "na"), port=port,
                            cert_file=cert_file or "", private_key_file=private_key_path)
        if r.get("ok") and r.get("new_refresh_token"):
            cfgmod.save(config_path, {"tesla": {"shim_refresh_token": r["new_refresh_token"]}})
        return JSONResponse(r)

    routes = [
        Route("/", index),
        Route("/api/config", get_config, methods=["GET"]),
        Route("/api/config", post_config, methods=["POST"]),
        Route("/api/keypair", post_keypair, methods=["POST"]),
        Route("/api/oauth/url", oauth_url, methods=["GET"]),
        Route("/api/oauth/exchange", oauth_exchange, methods=["POST"]),
        Route("/api/partner", partner, methods=["POST"]),
        Route("/api/npm/cert", npm_cert, methods=["POST"]),
        Route("/api/npm/pubkey-host", npm_pubkey_host, methods=["POST"]),
        Route("/api/npm/stream", npm_stream, methods=["POST"]),
        Route("/api/send-config", send_config, methods=["POST"]),
    ]
    if store is not None:
        routes.append(Route("/api/state", state, methods=["GET"]))
    return Starlette(routes=routes)
