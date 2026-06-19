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

import elevation as elevation_mod
import records
from app import reclog
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


def start_ingest(store, records_file, capture=None):
    """Background thread: the single records tail that feeds the Store (and thus every sink).

    The tail is the lifeline of the whole app — every surface reads from the Store it feeds. It must
    never die: records.tail() already swallows I/O errors, and we additionally isolate each record so
    one malformed payload can't take down the thread, and restart the loop if it ever exits.

    `capture`, if given, is a reclog.RecordLog that tees every record to the persistent /data volume
    (the live records file is on tmpfs and lost at boot) so past drives can be parsed."""
    def run():
        while True:
            try:
                for rec in records.tail(records_file):
                    if capture is not None:
                        capture.write(rec)
                    try:
                        store.ingest(rec)
                    except Exception as exc:   # one bad record must not kill the tail
                        print(f"[app] ingest error on record: {exc!r}", flush=True)
            except Exception as exc:           # tail() shouldn't raise, but never let it be fatal
                print(f"[app] records tail crashed: {exc!r}; restarting in 2s", flush=True)
                time.sleep(2)
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


def start_prime(registry, config_path, interval=1800, fleet_log=None):
    """Fleet-API cold-start prime, re-run periodically so creds set later (after OAuth) are picked
    up without restarting the app. Reads creds live from config (decoupled from run.sh env).

    After each prime, auto-resend fleet_telemetry_config if the requested-field roster changed since
    the last successful send (e.g. across an upgrade that added fields) — see app.control.autosend.

    `fleet_log`, if given, is a reclog.RecordLog the recurring Fleet calls (token refresh, /products,
    /vehicle_data) are appended to (secrets redacted) for correlation with the telemetry log."""
    from app.control import autosend
    from app.control import config as cfgmod
    from app.control import fleetlog
    from app.control import tesla
    auth = os.environ.get("FT_SHIM_AUTH_HOST", "https://auth.tesla.com")
    shim_state = os.environ.get("FT_SHIM_STATE", "/data/shim-state.json")
    wizard_state = os.environ.get("FT_WIZARD_STATE", "/data/wizard-state.json")
    cert_file = os.environ.get("FT_CERT_FILE", "/data/certs/server.crt")
    priv = os.environ.get("FT_PRIVATE_KEY", "/data/keys/private-key.pem")
    logged_post_form = fleetlog.wrap_post_form(fleet_log, prime._post_form)
    logged_get = fleetlog.wrap_get(fleet_log, prime._get)

    def run():
        while True:
            c = cfgmod.load(config_path).get("tesla", {})
            cid = c.get("client_id", "")
            rt = _load_refresh_token() or c.get("shim_refresh_token", "")
            if cid and rt:
                prime.prime_once(registry, client_id=cid, refresh_token=rt, auth_host=auth,
                                 fleet_host=tesla.fleet_host(c.get("region", "na")),
                                 on_token=_save_refresh_token,
                                 post_form=logged_post_form, get=logged_get)
                autosend.maybe_resend(vins=registry.vins(), config_path=config_path,
                                      shim_state_path=shim_state, wizard_state_path=wizard_state,
                                      cert_file=cert_file, private_key_path=priv, auth_host=auth,
                                      log=lambda m: print(m, flush=True))
            time.sleep(interval)
    threading.Thread(target=run, daemon=True).start()


async def _serve(app, port):
    await uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")).serve()


async def _supervise(name, factory):
    """Run a long-lived coroutine forever; if it raises, log and restart it. Isolates the listeners
    so a crash in one surface (shim, ws, dashboard) can't take down the others (shared-fate was #2)."""
    while True:
        try:
            await factory()
            print(f"[app] {name} exited cleanly; restarting in 2s", flush=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[app] {name} crashed: {exc!r}; restarting in 2s", flush=True)
        await asyncio.sleep(2)


async def run(store, *, shim_app, ingress_app, pubkey_app, shim_port, ws_port, web_port, pubkey_port,
              elevation=None):
    sink = stream.StreamSink(store, elevation=elevation)

    async def ws_server():
        server = await websockets.serve(sink.handler, "0.0.0.0", ws_port)
        await server.wait_closed()

    print(f"[app] dashboard+wizard :{web_port} · shim :{shim_port} · stream ws :{ws_port} · pubkey :{pubkey_port}",
          flush=True)
    await asyncio.gather(
        _supervise("ingress", lambda: _serve(ingress_app, web_port)),
        _supervise("shim", lambda: _serve(shim_app, shim_port)),
        _supervise("pubkey", lambda: _serve(pubkey_app, pubkey_port)),
        _supervise("stream-sink", sink.run),
        _supervise("stream-ws", ws_server),
    )


def main():
    cfg_path = os.environ.get("FT_WIZARD_CONFIG", "/data/wizard-config.json")
    priv = os.environ.get("FT_PRIVATE_KEY", "/data/keys/private-key.pem")
    pub = os.environ.get("FT_PUBLIC_KEY", "/data/keys/public-key.pem")
    cert_file = os.environ.get("FT_CERT_FILE", "/data/certs/server.crt")
    store, registry, shim_app = build()
    # Local DEM resolver fills the elevation that no Tesla API provides — both the TeslaMate stream
    # column (meters) and the dashboard (m/ft toggle). On by default; tiles cache to the persistent
    # /data volume. Disable with FT_ELEVATION=0.
    elev = None
    if os.environ.get("FT_ELEVATION", "1") != "0":
        elev = elevation_mod.Resolver(os.environ.get("FT_ELEVATION_DIR", "/data/elevation"))
        elev.load_disk()
    ingress_app = wizard.build_wizard_app(
        config_path=cfg_path,
        wizard_state_path=os.environ.get("FT_WIZARD_STATE", "/data/wizard-state.json"),
        shim_state_path=os.environ.get("FT_SHIM_STATE", "/data/shim-state.json"),
        private_key_path=priv, public_key_path=pub,
        cert_file=cert_file, certs_dir=os.path.dirname(cert_file),
        store=store, registry=registry,
        version=os.environ.get("FT_ADDON_VERSION", ""), namespace="tesla_telemetry", elevation=elev)
    pubkey_app = build_pubkey_app(pub)
    # Append-only persistent capture of every record (the live tmpfs records file is wiped at boot),
    # so past drives can be parsed. Never rotated/truncated/deleted by the add-on — removed only on
    # uninstall (/data wipe). Path empty -> disabled.
    capture = None
    cap_path = os.environ.get("FT_TELEMETRY_LOG", "/data/telemetry-log.jsonl")
    if cap_path:
        capture = reclog.RecordLog(cap_path)
    start_ingest(store, os.environ.get("FT_RECORDS_FILE", "/tmp/ft-records.jsonl"), capture=capture)
    # Parallel append-only log of every recurring Fleet API call (secrets redacted), same durability
    # guarantees as the telemetry log. Path empty -> disabled.
    fleet_log = None
    fl_path = os.environ.get("FT_FLEET_LOG", "/data/fleet-log.jsonl")
    if fl_path:
        fleet_log = reclog.RecordLog(fl_path)
    start_prime(registry, cfg_path, fleet_log=fleet_log)
    asyncio.run(run(store, shim_app=shim_app, ingress_app=ingress_app, pubkey_app=pubkey_app,
                    shim_port=int(os.environ.get("FT_SHIM_PORT", "8085")),
                    ws_port=int(os.environ.get("FT_WS_PORT", "8081")),
                    web_port=int(os.environ.get("FT_WEB_PORT", "8099")),
                    pubkey_port=int(os.environ.get("FT_PUBKEY_PORT", "8100")),
                    elevation=elev))


if __name__ == "__main__":
    main()
