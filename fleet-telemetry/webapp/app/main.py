#!/usr/bin/env python3
"""Unified Fleet Telemetry app process: one records tail feeds one Store; the Fleet-API shim (HTTP)
and the TeslaMate streaming ws are served from it — replacing the separate shim.py + ws_stream.py
processes (and their two independent tails) with one.

Ports/paths come from the environment (set by run.sh):
  FT_RECORDS_FILE  records file to tail
  FT_SHIM_PORT     Fleet-API shim HTTP port (default 8085)
  FT_WS_PORT       TeslaMate streaming ws port (default 8081)
"""
import asyncio
import json
import os
import threading

import uvicorn
import websockets

import records
from app import state
from app.control import prime
from app.sinks import shim_rest, stream


def build():
    store = state.Store()
    registry = shim_rest.Registry(store)
    app = shim_rest.build_app(store, registry)
    return store, registry, app


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


def start_prime(registry):
    """One-shot Fleet-API cold-start prime in a background thread (blocking urllib)."""
    cid = os.environ.get("FT_SHIM_CLIENT_ID", "")
    rt = _load_refresh_token() or os.environ.get("FT_SHIM_REFRESH_TOKEN", "")
    auth = os.environ.get("FT_SHIM_AUTH_HOST", "https://auth.tesla.com")
    fleet = os.environ.get("FT_SHIM_FLEET_HOST", "https://fleet-api.prd.na.vn.cloud.tesla.com")

    def run():
        prime.prime_once(registry, client_id=cid, refresh_token=rt, auth_host=auth,
                         fleet_host=fleet, on_token=_save_refresh_token)
    threading.Thread(target=run, daemon=True).start()


async def run(store, app, shim_port, ws_port):
    sink = stream.StreamSink(store)
    bus_task = asyncio.create_task(sink.run())
    ws_server = await websockets.serve(sink.handler, "0.0.0.0", ws_port)
    print(f"[app] TeslaMate streaming ws on :{ws_port}/streaming/", flush=True)
    config = uvicorn.Config(app, host="0.0.0.0", port=shim_port, log_level="warning")
    server = uvicorn.Server(config)
    print(f"[app] Fleet-API shim on :{shim_port}", flush=True)
    try:
        await server.serve()
    finally:
        bus_task.cancel()
        ws_server.close()


def main():
    store, registry, app = build()
    start_ingest(store, os.environ.get("FT_RECORDS_FILE", "/tmp/ft-records.jsonl"))
    start_prime(registry)
    asyncio.run(run(store, app,
                    int(os.environ.get("FT_SHIM_PORT", "8085")),
                    int(os.environ.get("FT_WS_PORT", "8081"))))


if __name__ == "__main__":
    main()
