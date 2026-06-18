"""Ingress web app: dashboard + setup wizard, exposing the exact endpoint contract the (verbatim
v0) dashboard/wizard pages call — /setup, /api/state, and /api/wizard/*.
"""
import json
import os
import re

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

from app.control import checks, config as cfgmod, hostports, keys, npm, sendconfig, tesla, tokens
from app.web import api

_STATIC = os.path.join(os.path.dirname(__file__), "static")
TELEMETRY_PORT = int(os.environ.get("FT_TELEMETRY_PORT", "4443"))
PUBKEY_PORT = int(os.environ.get("FT_PUBKEY_PORT", "8100"))


async def _json_body(req):
    raw = await req.body()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return None


def build_wizard_app(*, config_path, wizard_state_path, shim_state_path, private_key_path, public_key_path,
                     cert_file, certs_dir, store, registry, version="", namespace="tesla_telemetry"):
    with open(os.path.join(_STATIC, "wizard.html")) as fh:
        wizard_page = fh.read()
    with open(os.path.join(_STATIC, "dashboard.html")) as fh:
        dash_page = fh.read()

    def cfg():
        return cfgmod.load(config_path)

    # --- pages -------------------------------------------------------------------------
    async def index(_req):
        st = cfgmod.load_wizard_state(wizard_state_path)
        if not st.get("completed"):
            return RedirectResponse("./setup")
        return HTMLResponse(dash_page)

    async def setup(_req):
        return HTMLResponse(wizard_page)

    # --- dashboard data ----------------------------------------------------------------
    async def state(_req):
        return JSONResponse(api.state_payload(store, version=version, cert=checks.cert_detail(cert_file),
                                              namespace=namespace))

    # --- wizard reads ------------------------------------------------------------------
    async def wiz_state(_req):
        return JSONResponse(cfgmod.load_wizard_state(wizard_state_path))

    async def get_config(_req):
        return JSONResponse(cfgmod.redacted(config_path))

    async def host_ports(_req):
        return JSONResponse({"telemetry_host_port": hostports.addon_host_port(TELEMETRY_PORT),
                             "pubkey_host_port": hostports.addon_host_port(PUBKEY_PORT)})

    # --- wizard writes / actions -------------------------------------------------------
    async def wiz_save(req):
        patch = await _json_body(req)
        if not isinstance(patch, dict):
            return JSONResponse({"error": "payload must be a JSON object"}, status_code=400)
        cfgmod.save_wizard_state(wizard_state_path, patch)
        return JSONResponse({"ok": True})

    async def post_config(req):
        patch = await _json_body(req)
        if not isinstance(patch, dict):
            return JSONResponse({"error": "payload must be a JSON object"}, status_code=400)
        try:
            cfgmod.save(config_path, cfgmod.strip_secret_masks(patch))
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse({"ok": True, "config": cfgmod.redacted(config_path)})

    async def keypair(req):
        body = await _json_body(req) or {}
        force = bool(body.get("force"))
        c = cfg()["tesla"]
        # Don't silently regenerate a key that's already registered (would invalidate registration).
        if c.get("partner_registered") and not force and os.path.exists(private_key_path) and os.path.exists(public_key_path):
            return JSONResponse({"ok": True, "already": True, "fingerprint": keys.key_fingerprint(private_key_path)})
        r = keys.generate_keypair(private_key_path, public_key_path, force=force)
        if r.get("ok") and not r.get("already"):
            cfgmod.save(config_path, {"tesla": {"keypair_generated": True}})
        return JSONResponse(r)

    async def npm_proxy_host(_req):
        c, n = cfg()["tesla"], cfg()["npm"]
        r = npm.create_pubkey_host(base=n.get("url", ""), email=n.get("email", ""), password=n.get("password", ""),
                                   domain=c.get("pubkey_domain", ""), forward_host=n.get("forward_host", ""),
                                   forward_port=hostports.addon_host_port(PUBKEY_PORT))
        if r.get("ok") and r.get("id") is not None:
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
                              forward_port=hostports.addon_host_port(TELEMETRY_PORT))
        if r.get("ok") and r.get("id") is not None:
            cfgmod.save(config_path, {"npm": {"stream_id": r["id"]}})
        return JSONResponse(r)

    async def register_partner(_req):
        c = cfg()["tesla"]
        r = tesla.register_partner(client_id=c.get("client_id", ""), client_secret=c.get("client_secret", ""),
                                   domain=c.get("pubkey_domain", ""), region=c.get("region", "na"))
        if r.get("ok"):
            cfgmod.save(config_path, {"tesla": {"partner_registered": True}})
        return JSONResponse(r)

    async def oauth_url(req):
        body = await _json_body(req) or {}
        redirect_uri = str(body.get("redirect_uri", "")).strip()
        state_param = str(body.get("state", "")).strip()
        if not redirect_uri or not state_param:
            return JSONResponse({"error": "redirect_uri and state are required"}, status_code=400)
        c = cfg()["tesla"]
        if not c.get("client_id"):
            return JSONResponse({"error": "Set your Tesla client_id first (step 3)."}, status_code=400)
        return JSONResponse({"url": tesla.authorize_url(c["client_id"], redirect_uri, state_param)})

    async def oauth_exchange(req):
        body = await _json_body(req) or {}
        c = cfg()["tesla"]
        r = tesla.exchange_code(client_id=c.get("client_id", ""), client_secret=c.get("client_secret", ""),
                                code=str(body.get("code", "")).strip(),
                                redirect_uri=str(body.get("redirect_uri", "")).strip(),
                                region=c.get("region", "na"))
        if r.get("ok"):
            cfgmod.save(config_path, {"tesla": {"shim_refresh_token": r["refresh_token"]}})
            return JSONResponse({"ok": True})
        return JSONResponse(r)

    async def check(req):
        body = await _json_body(req) or {}
        kind = body.get("check")
        if kind == "pubkey":
            domain = re.sub(r"^https?://", "", str(body.get("domain", "")).strip()).split("/")[0]
            if not domain:
                return JSONResponse({"error": "domain required"}, status_code=400)
            return JSONResponse(checks.check_pubkey(domain))
        if kind == "cert":
            return JSONResponse(checks.cert_detail(cert_file))
        if kind == "records":
            with store._lock:
                total, last = store.total_records, store.last_record_epoch
            return JSONResponse({"ok": total > 0, "total": total, "last_epoch": last, "vins": store.vins()})
        if kind == "send_telemetry_config":
            c = cfg()["tesla"]
            domain = re.sub(r"^https?://", "", str(body.get("domain") or c.get("telemetry_domain") or "").strip()).split("/")[0]
            region = str(body.get("region") or c.get("region") or "na").strip()
            try:
                port = int(body.get("port") or c.get("telemetry_port") or 4443)
            except (TypeError, ValueError):
                port = 4443
            refresh_token = tokens.load(shim_state_path) or c.get("shim_refresh_token", "")
            r = sendconfig.send(vins=registry.vins() if registry else [], client_id=c.get("client_id", ""),
                                refresh_token=refresh_token, domain=domain, region=region,
                                port=port, cert_file=cert_file, private_key_file=private_key_path)
            if r.get("ok") and r.get("new_refresh_token"):
                # Rotated token -> shim-state (NOT the watched config; would bounce the binary).
                tokens.save(shim_state_path, r["new_refresh_token"])
            return JSONResponse(r)
        return JSONResponse({"error": "unknown check type"}, status_code=400)

    return Starlette(routes=[
        Route("/", index),
        Route("/setup", setup),
        Route("/api/state", state),
        Route("/api/wizard/state", wiz_state),
        Route("/api/wizard/config", get_config, methods=["GET"]),
        Route("/api/wizard/hostports", host_ports),
        Route("/api/wizard/save", wiz_save, methods=["POST"]),
        Route("/api/wizard/config", post_config, methods=["POST"]),
        Route("/api/wizard/keypair", keypair, methods=["POST"]),
        Route("/api/wizard/npm-proxy-host", npm_proxy_host, methods=["POST"]),
        Route("/api/wizard/npm-stream", npm_stream, methods=["POST"]),
        Route("/api/wizard/register-partner", register_partner, methods=["POST"]),
        Route("/api/wizard/oauth-url", oauth_url, methods=["POST"]),
        Route("/api/wizard/oauth-exchange", oauth_exchange, methods=["POST"]),
        Route("/api/wizard/check", check, methods=["POST"]),
    ])
