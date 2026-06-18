"""Dashboard data API — builds the /api/state payload from the unified Store.

Shape matches the v0 dashboard so the existing dashboard JS consumes it unchanged once served from
the app.
"""
import time

import fields


def state_payload(store, *, version="", cert=None, namespace="", start_time=0.0):
    now = time.time()
    with store._lock:
        items = [(vin, {
            "fields": dict(v["fields"]),
            "display_name": v.get("display_name") or vin,
            "client_version": v.get("client_version"),
            "soc": [round(val, 2) for _, val in v["history"]["soc"]],
            "speed": [round(val, 2) for _, val in v["history"]["speed"]],
        }) for vin, v in store.vehicles.items()]
        total = store.total_records
        last = store.last_record_epoch
    vehicles = []
    for vin, v in items:
        f = v["fields"]
        lat = lon = None
        if "Location" in f:
            lat, lon = fields.parse_location(f["Location"]["value"])
        last_seen = max((x["received_at"] for x in f.values()), default=0)
        vehicles.append({
            "vin": vin, "display_name": v["display_name"], "fields": f,
            "location": {"lat": lat, "lon": lon},
            "soc_history": v["soc"], "speed_history": v["speed"],
            "client_version": v["client_version"], "last_seen_epoch": last_seen,
        })
    return {"now": now, "uptime_seconds": (now - start_time) if start_time else 0,
            "total_records": total, "records_per_min": store.rate_per_min(),
            "last_record_epoch": last, "namespace": namespace,
            "version": version, "cert": cert or {}, "vehicles": vehicles}
