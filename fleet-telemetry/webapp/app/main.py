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
import os
import threading

import uvicorn
import websockets

import records
from app import state
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
    asyncio.run(run(store, app,
                    int(os.environ.get("FT_SHIM_PORT", "8085")),
                    int(os.environ.get("FT_WS_PORT", "8081"))))


if __name__ == "__main__":
    main()
