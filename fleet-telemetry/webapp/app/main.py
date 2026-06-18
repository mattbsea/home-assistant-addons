#!/usr/bin/env python3
"""Unified Fleet Telemetry app: one process, one records tail feeding one Store, serving every
surface — the ingress dashboard+wizard (:web), the Fleet-API shim (:shim), the TeslaMate streaming
ws (:ws), and the public-key .well-known listener (:pubkey). Replaces the v0 server.py + shim.py +
ws_stream.py + bridge.py processes.
"""
import asyncio
import json
import os
import threading
import time

import uvicorn
import websockets
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

import records
from app import state
from app.control import prime
from app.sinks import shim_rest, stream
from app.web import wizard

PUBKEY_WELL_KNOWN = "/.well-known/appspecific/com.tesla.3p.public-key.pem"


def build_pubkey_app(public_key_path):
    async def pubkey(_req):
        try:
            with open(public_key_path, "rb") as fh:
                return Response(fh.read(), media_type="application/x-pem-file")
        except OSError:
            return PlainTextResponse("public key not generated yet", status_code=404)
    return Starlette(routes=[Route(PUBKEY_WELL_KNOWN, pubkey)])


def build():
    store = state.Store()
    registry = shim_rest.Registry(store)
    shim_app = shim_rest.build_app(store, registry)
    return store, registry, shim_app


def start_ingest(store, records_file):
    """Background thread: the single records tail that feeds the Store (and thus every sink)."""
    def run():
        for rec in records.tail(records_file):
            store.ingest(rec)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def _state_path():
    return os.environ.get("FT_SHIM_STATE", "/data/shim-state.json")


def _load_refresh_token():
    try:
        with open(_state_path()) as fh:
            return json.load(fh).get("refresh_token", "")
    except (OSError, ValueError):
        return ""


def _save_refresh_token(rt):
    p = _state_path()
    try:
        data = {}
        try:
            with open(p) as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        data["refresh_token"] = rt
        with open(p + ".tmp", "w") as fh:
            json.dump(data, fh)
        os.replace(p + ".tmp", p)
    except OSError:
        pass


def start_prime(registry, config_path, interval=1800):
    """Fleet-API cold-start prime, re-run periodically so creds set later (after OAuth) are picked
    up without restarting the app. Reads creds live from config (decoupled from run.sh env)."""
    from app.control import config as cfgmod
    from app.control import tesla
    auth = os.environ.get("FT_SHIM_AUTH_HOST", "https://auth.tesla.com")

    def run():
        while True:
            c = cfgmod.load(config_path).get("tesla", {})
            cid = c.get("client_id", "")
            rt = _load_refresh_token() or c.get("shim_refresh_token", "")
            if cid and rt:
                prime.prime_once(registry, client_id=cid, refresh_token=rt, auth_host=auth,
                                 fleet_host=tesla.fleet_host(c.get("region", "na")),
                                 on_token=_save_refresh_token)
            time.sleep(interval)
    threading.Thread(target=run, daemon=True).start()


async def _serve(app, port):
    await uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")).serve()


async def run(store, *, shim_app, ingress_app, pubkey_app, shim_port, ws_port, web_port, pubkey_port):
    sink = stream.StreamSink(store)
    asyncio.create_task(sink.run())
    await websockets.serve(sink.handler, "0.0.0.0", ws_port)
    print(f"[app] dashboard+wizard :{web_port} · shim :{shim_port} · stream ws :{ws_port} · pubkey :{pubkey_port}",
          flush=True)
    await asyncio.gather(
        _serve(ingress_app, web_port),
        _serve(shim_app, shim_port),
        _serve(pubkey_app, pubkey_port),
    )


def main():
    cfg_path = os.environ.get("FT_WIZARD_CONFIG", "/data/wizard-config.json")
    priv = os.environ.get("FT_PRIVATE_KEY", "/data/keys/private-key.pem")
    pub = os.environ.get("FT_PUBLIC_KEY", "/data/keys/public-key.pem")
    cert_file = os.environ.get("FT_CERT_FILE", "/data/certs/server.crt")
    store, registry, shim_app = build()
    ingress_app = wizard.build_wizard_app(
        config_path=cfg_path, private_key_path=priv, public_key_path=pub,
        cert_file=cert_file, certs_dir=os.path.dirname(cert_file), registry=registry,
        store=store, version=os.environ.get("FT_ADDON_VERSION", ""), namespace="tesla_telemetry")
    pubkey_app = build_pubkey_app(pub)
    start_ingest(store, os.environ.get("FT_RECORDS_FILE", "/tmp/ft-records.jsonl"))
    start_prime(registry, cfg_path)
    asyncio.run(run(store, shim_app=shim_app, ingress_app=ingress_app, pubkey_app=pubkey_app,
                    shim_port=int(os.environ.get("FT_SHIM_PORT", "8085")),
                    ws_port=int(os.environ.get("FT_WS_PORT", "8081")),
                    web_port=int(os.environ.get("FT_WEB_PORT", "8099")),
                    pubkey_port=int(os.environ.get("FT_PUBKEY_PORT", "8100"))))


if __name__ == "__main__":
    main()
