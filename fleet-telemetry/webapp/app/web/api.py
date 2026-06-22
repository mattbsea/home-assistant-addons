"""Dashboard data API — builds the /api/state payload from the unified Store.

Shape matches the v0 dashboard so the existing dashboard JS consumes it unchanged once served from
the app.
"""
import asyncio
import json
import time

import fields


def state_payload(store, *, version="", cert=None, namespace="", start_time=0.0, elevation_resolver=None):
    now = time.time()
    with store._lock:
        meta = [(vin, {
            "display_name": v.get("display_name") or vin,
            "client_version": v.get("client_version"),
            "soc": [round(val, 2) for _, val in v["history"]["soc"]],
            "speed": [round(val, 2) for _, val in v["history"]["speed"]],
            # freshness reflects the most-recent field write (telemetry, mostly)
            "last_seen_epoch": max((x["received_at"] for x in v["fields"].values()), default=0),
            "seed_epoch": v.get("seed_epoch", 0.0),
        }) for vin, v in store.vehicles.items()]
        total = store.total_records
        last = store.last_record_epoch
    vehicles = []
    for vin, m in meta:
        # The unified per-VIN field map (telemetry overlaid on the Fleet seed).
        f = store.fields_view(vin)
        lat = lon = None
        if "Location" in f:
            lat, lon = fields.parse_location(f["Location"]["value"])
        # Elevation is not in any Tesla API; derive it from the local DEM (meters, the canonical unit
        # TeslaMate stores). The dashboard converts to feet when set to imperial. None until the 1°
        # tile for this position is cached (it downloads in the background on first lookup).
        if elevation_resolver is not None and lat is not None and lon is not None:
            elev_m = elevation_resolver.elevation(lat, lon)
            if elev_m is not None:
                f["Elevation"] = {"value": elev_m, "created_at": "", "received_at": now, "source": "derived"}
        vehicles.append({
            "vin": vin, "display_name": m["display_name"], "fields": f,
            "state": store.vehicle_state(vin),   # online / asleep / offline (authoritative)
            "location": {"lat": lat, "lon": lon},
            "soc_history": m["soc"], "speed_history": m["speed"],
            "client_version": m["client_version"], "last_seen_epoch": m["last_seen_epoch"],
            "seed_epoch": m["seed_epoch"],
        })
    return {"now": now, "uptime_seconds": (now - start_time) if start_time else 0,
            "total_records": total, "records_per_min": store.rate_per_min(),
            "last_record_epoch": last, "namespace": namespace,
            "version": version, "cert": cert or {}, "vehicles": vehicles,
            # Fleet-API calls made since add-on start (in-memory; resets on restart).
            "fleet_api": {**store.fleet_calls(), "since": start_time}}


async def console_stream(store, *, idle_timeout=20.0):
    """SSE generator for the raw-telemetry Console: push ONE event per incoming record, carrying the
    raw changed fields exactly as they land (no coalescing, unlike the dashboard feed). Subscribes to
    the Store event bus and always unsubscribes on close; heartbeat comment when idle keeps the proxy
    connection open."""
    loop = asyncio.get_running_loop()
    q = store.subscribe(loop)
    try:
        yield ": connected\n\n"                                  # immediate: opens the stream + confirms subscribe
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=idle_timeout)
            except asyncio.TimeoutError:
                yield "event: hb\ndata: 1\n\n"
                continue
            yield "data: " + json.dumps({"vin": ev.get("vin"), "created_at": ev.get("created_at"),
                                         "at": ev.get("at"), "changed": ev.get("changed", {})}) + "\n\n"
    finally:
        store.unsubscribe(q)


async def sse_stream(store, payload_fn, *, idle_timeout=20.0, coalesce=0.2):
    """SSE generator for the dashboard push feed: emit an initial snapshot, then one event per Store
    change (bursts coalesced into a single payload), with a heartbeat comment when idle so the ingress
    proxy keeps the connection open. ``payload_fn`` builds the JSON payload (state_payload). Subscribes
    to the Store's event bus and always unsubscribes on close."""
    loop = asyncio.get_running_loop()
    q = store.subscribe(loop)
    try:
        yield "data: " + json.dumps(payload_fn()) + "\n\n"      # initial snapshot, no wait
        while True:
            try:
                await asyncio.wait_for(q.get(), timeout=idle_timeout)
            except asyncio.TimeoutError:
                yield "event: hb\ndata: 1\n\n"                  # heartbeat: keeps ingress open AND
                continue                                       # lets the client detect a silent stall
            if coalesce:
                await asyncio.sleep(coalesce)                   # let a burst of field updates pile up
            while not q.empty():
                q.get_nowait()
            yield "data: " + json.dumps(payload_fn()) + "\n\n"
    finally:
        store.unsubscribe(q)
