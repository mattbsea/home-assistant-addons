"""Dashboard data API — builds the /api/state payload from the unified Store.

Shape matches the v0 dashboard so the existing dashboard JS consumes it unchanged once served from
the app.
"""
import time

import fields


def state_payload(store, *, version="", cert=None, namespace="", start_time=0.0):
    now = time.time()
    with store._lock:
        meta = [(vin, {
            "display_name": v.get("display_name") or vin,
            "client_version": v.get("client_version"),
            "soc": [round(val, 2) for _, val in v["history"]["soc"]],
            "speed": [round(val, 2) for _, val in v["history"]["speed"]],
            # freshness reflects LIVE telemetry only, not the (slower) prime snapshot
            "last_seen_epoch": max((x["received_at"] for x in v["fields"].values()), default=0),
            "prime_epoch": v.get("prime_epoch", 0.0),
        }) for vin, v in store.vehicles.items()]
        total = store.total_records
        last = store.last_record_epoch
    vehicles = []
    for vin, m in meta:
        # The superset: live telemetry overlaid on the Fleet-API prime (see Store.merged_fields).
        f = store.merged_fields(vin)
        lat = lon = None
        if "Location" in f:
            lat, lon = fields.parse_location(f["Location"]["value"])
        vehicles.append({
            "vin": vin, "display_name": m["display_name"], "fields": f,
            "location": {"lat": lat, "lon": lon},
            "soc_history": m["soc"], "speed_history": m["speed"],
            "client_version": m["client_version"], "last_seen_epoch": m["last_seen_epoch"],
            "prime_epoch": m["prime_epoch"],
        })
    return {"now": now, "uptime_seconds": (now - start_time) if start_time else 0,
            "total_records": total, "records_per_min": store.rate_per_min(),
            "last_record_epoch": last, "namespace": namespace,
            "version": version, "cert": cert or {}, "vehicles": vehicles}
