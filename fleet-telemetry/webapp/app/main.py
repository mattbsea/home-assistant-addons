#!/usr/bin/env python3
"""Unified Fleet Telemetry app: one process, one records tail feeding one Store, serving every
surface — the ingress dashboard+wizard (:web), the Fleet-API shim (:shim), the TeslaMate streaming
ws (:ws), and the public-key .well-known listener (:pubkey). Replaces the v0 server.py + shim.py +
ws_stream.py + bridge.py processes.
"""
import asyncio
import json
import os
import queue
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
from app.control import monitor, prime
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


def start_prime(registry, config_path, fleet_log=None, seed_retry_secs=120):
    """Fleet-API seed + charge-field worker (the Store is the single source of truth thereafter).

    Two daemon threads:
      - **seed**: call Fleet-API vehicle_data ONCE to seed the SSOT, retrying only until the first
        successful seed (creds may arrive after the wizard's OAuth; the car may be asleep at boot).
        After it succeeds, no recurring poll — telemetry owns the structure. Auto-resends
        fleet_telemetry_config if the requested-field roster changed (e.g. across an upgrade).
      - **charge**: block on the Store's charge-start signal and do ONE targeted Fleet fetch of the
        two non-streamed charge fields (charger_pilot_current / fast_charger_brand) per session.

    `fleet_log`, if given, is a reclog.RecordLog the Fleet calls are appended to (secrets redacted)."""
    from app.control import autosend
    from app.control import config as cfgmod
    from app.control import fleetlog
    from app.control import tesla
    auth = os.environ.get("FT_SHIM_AUTH_HOST", "https://auth.tesla.com")
    shim_state = os.environ.get("FT_SHIM_STATE", "/data/shim-state.json")
    wizard_state = os.environ.get("FT_WIZARD_STATE", "/data/wizard-state.json")
    cert_file = os.environ.get("FT_CERT_FILE", "/data/certs/server.crt")
    priv = os.environ.get("FT_PRIVATE_KEY", "/data/keys/private-key.pem")
    # Count every Fleet-API call at the request choke point. The counter wrapper is INNERMOST — under
    # the fleet-log wrapper — so it keeps working when the diagnostic logs are eventually removed.
    def _count_call(fn):
        def w(url, *a, **k):
            registry.store.note_fleet_call(url)
            return fn(url, *a, **k)
        return w
    logged_post_form = fleetlog.wrap_post_form(fleet_log, _count_call(prime._post_form))
    logged_get = fleetlog.wrap_get(fleet_log, _count_call(prime._get))
    settle_secs = int(os.environ.get("FT_SLEEP_SETTLE_SECS", "60"))
    bridge_secs = int(os.environ.get("FT_BRIDGE_POLL_SECS", "300"))

    def _creds():
        c = cfgmod.load(config_path).get("tesla", {})
        return (c.get("client_id", ""), _load_refresh_token() or c.get("shim_refresh_token", ""),
                tesla.fleet_host(c.get("region", "na")))

    def seed_loop():
        while True:
            cid, rt, fleet_host = _creds()
            if cid and rt:
                n = prime.prime_once(registry, client_id=cid, refresh_token=rt, auth_host=auth,
                                     fleet_host=fleet_host, on_token=_save_refresh_token,
                                     post_form=logged_post_form, get=logged_get)
                autosend.maybe_resend(vins=registry.vins(), config_path=config_path,
                                      shim_state_path=shim_state, wizard_state_path=wizard_state,
                                      cert_file=cert_file, private_key_path=priv, auth_host=auth,
                                      log=lambda m: print(m, flush=True))
                if n > 0:
                    print("[app] seed complete; telemetry now owns the store (no recurring poll)", flush=True)
                    return   # seeded ≥1 vehicle — stop; telemetry is the source of truth from here
            time.sleep(seed_retry_secs)   # creds not ready or car asleep — retry until first seed

    def charge_loop():
        while True:
            vin = registry.store.charge_starts.get()   # blocks until a charge session begins
            try:
                cid, rt, fleet_host = _creds()
                prime.fetch_charge_fields(registry.store, vin=vin, tesla_id=registry.store.tesla_id(vin),
                                          client_id=cid, refresh_token=rt, auth_host=auth, fleet_host=fleet_host,
                                          post_form=logged_post_form, get=logged_get, on_token=_save_refresh_token,
                                          log=lambda m: print(m, flush=True))
            except Exception as exc:   # a charge-fetch failure must never kill the worker
                print(f"[app] charge-fetch worker error: {exc!r}", flush=True)

    def stream_monitor():
        # Poll the Fleet API ONLY when the telemetry stream isn't delivering — bridging charge/state
        # while the stream is down (home WiFi handoff, network blip, restart-while-charging) and
        # confirming sleep. Zero Fleet calls while the stream is healthy. Fires early on a DISCONNECTED
        # nudge (with a settle window), otherwise ticks every bridge interval.
        quiet = max(90, settle_secs)
        max_bridge = max(1, int(12 * 3600 / max(bridge_secs, 1)))   # soft cap: ~12 h continuous bridging
        # While asleep/offline the car won't stream to clear the state, and Tesla can change
        # offline<->asleep<->online while silent — so re-confirm via /products (a no-wake call) on this
        # cadence instead of latching the first reading until telemetry happens to resume.
        sleep_recheck = max(bridge_secs, int(os.environ.get("FT_SLEEP_RECHECK_SECS", "1800")))
        bridged = {}   # vin -> consecutive bridge-poll count (reset when streaming resumes or asleep)
        _log = lambda m: print(m, flush=True)

        def check(vin, settle):
            def poll():
                cid, rt, fleet_host = _creds()   # read creds only when we actually call the Fleet API
                return prime.poll_vehicle(registry, vin=vin, client_id=cid, refresh_token=rt,
                                          auth_host=auth, fleet_host=fleet_host,
                                          post_form=logged_post_form, get=logged_get,
                                          on_token=_save_refresh_token, log=_log)
            monitor.bridge_or_confirm_sleep(
                registry.store, vin, settle=settle, streaming_quiet=quiet, settle_secs=settle_secs,
                bridged=bridged, max_bridge=max_bridge, sleep_recheck_secs=sleep_recheck,
                poll=poll, sleep=time.sleep, log=_log)

        while True:
            try:
                try:
                    vin, _disc = registry.store.sleep_checks.get(timeout=bridge_secs)
                    check(vin, settle=True)                       # DISCONNECTED nudge -> early check
                except queue.Empty:
                    for vin in registry.vins():
                        check(vin, settle=False)                  # periodic tick over all vehicles
            except Exception as exc:   # the monitor must never die
                print(f"[app] stream-monitor error: {exc!r}", flush=True)

    threading.Thread(target=seed_loop, daemon=True).start()
    threading.Thread(target=charge_loop, daemon=True).start()
    threading.Thread(target=stream_monitor, daemon=True).start()


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
    sink = stream.StreamSink(store, elevation=elevation,
                             elevation_ema_alpha=float(os.environ.get("FT_ELEVATION_EMA_ALPHA", "0.2")))

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
