"""Phase 3 — dashboard data API (/api/state) built from the Store."""
import asyncio
import importlib
import time

import conftest

state = importlib.import_module("app.state")
api = importlib.import_module("app.web.api")

VIN = "7SAYGDEE3PF884783"


def test_state_payload_shape():
    store = state.Store()
    for r in conftest.load_records():
        store.ingest(r)
    p = api.state_payload(store, version="1.0.0", cert={"days_left": 90}, namespace="tesla_telemetry")
    assert p["version"] == "1.0.0" and p["namespace"] == "tesla_telemetry"
    assert p["total_records"] == len(conftest.load_records())
    assert p["records_per_min"] >= 0
    assert len(p["vehicles"]) == 1
    v = p["vehicles"][0]
    assert v["vin"] == VIN
    assert v["fields"]["Soc"]["value"] == 51.85185185185185      # full {value,created_at,received_at}
    assert v["location"] == {"lat": 47.768839, "lon": -122.155053}
    assert 51.85 in v["soc_history"]            # history rounded to 2dp (matches v0)
    assert v["client_version"] == "1.2.0"                        # captured from record metadata
    assert v["last_seen_epoch"] > 0


class _FakeElevation:
    def __init__(self):
        self.calls = []

    def elevation(self, lat, lon):
        self.calls.append((lat, lon))
        return 55                       # canonical meters

def test_state_payload_injects_elevation_from_resolver():
    store = state.Store()
    for r in conftest.load_records():
        store.ingest(r)
    res = _FakeElevation()
    f = api.state_payload(store, elevation_resolver=res)["vehicles"][0]["fields"]
    assert f["Elevation"]["value"] == 55 and f["Elevation"]["source"] == "derived"
    assert res.calls == [(47.768839, -122.155053)]   # resolved at the vehicle's location

async def test_sse_stream_initial_snapshot_then_event():
    """sse_stream emits an immediate snapshot on connect, then a fresh payload on each Store change,
    and unsubscribes on close (no leaked bus subscribers)."""
    store = state.Store()
    store.ingest({"msg": "record_payload", "vin": "V", "data": {"Soc": 50.0}})
    calls = {"n": 0}

    def payload():
        calls["n"] += 1
        return {"vins": store.vins(), "seq": calls["n"]}

    gen = api.sse_stream(store, payload, idle_timeout=5, coalesce=0)
    first = await gen.__anext__()                       # initial snapshot, no wait
    assert first.startswith("data: ") and '"vins": ["V"]' in first
    assert len(store._subscribers) == 1                 # subscribed
    store.ingest({"msg": "record_payload", "vin": "V2", "data": {"Soc": 1.0}})  # publishes a change
    nxt = await asyncio.wait_for(gen.__anext__(), 2)
    assert nxt.startswith("data: ") and '"V2"' in nxt   # pushed a fresh payload on the event
    await gen.aclose()
    assert store._subscribers == []                     # cleaned up on close


async def test_console_stream_emits_each_raw_record():
    """The console feed pushes one SSE event per incoming telemetry record, carrying the raw changed
    fields exactly as they arrive (no coalescing), and unsubscribes on close."""
    import json as _json
    store = state.Store()
    gen = api.console_stream(store, idle_timeout=5)
    hello = await gen.__anext__()                       # immediate connect marker -> confirms subscribe
    assert hello.startswith(":")
    assert len(store._subscribers) == 1
    store.ingest({"msg": "record_payload", "vin": "V",
                  "data": {"CreatedAt": "2026-06-22T20:00:00Z", "PackCurrent": 12.3, "PackVoltage": 380.0}})
    msg = await asyncio.wait_for(gen.__anext__(), 2)
    assert msg.startswith("data: ")
    payload = _json.loads(msg[6:])
    assert payload["vin"] == "V"
    assert payload["created_at"] == "2026-06-22T20:00:00Z"
    assert payload["changed"] == {"PackCurrent": 12.3, "PackVoltage": 380.0}
    await gen.aclose()
    assert store._subscribers == []                     # cleaned up on close


def test_state_payload_uptime_from_start_time():
    store = state.Store()
    p = api.state_payload(store, start_time=time.time() - 125)
    assert 120 <= p["uptime_seconds"] <= 135          # real uptime, not the always-0 default
    assert api.state_payload(store)["uptime_seconds"] == 0   # no start_time -> 0 (unchanged default)


def test_state_payload_no_elevation_without_resolver():
    store = state.Store()
    for r in conftest.load_records():
        store.ingest(r)
    f = api.state_payload(store)["vehicles"][0]["fields"]   # default: no resolver
    assert "Elevation" not in f


def _prime():
    return {k: conftest.load_reference("shim_vehicle_data.json")["response"].get(k)
            for k in ("drive_state", "charge_state", "climate_state", "vehicle_state", "vehicle_config")}


def test_dashboard_shows_superset_from_seed_when_telemetry_sparse():
    """A freshly-restarted/parked car (almost nothing streamed yet) still shows a full dashboard,
    because the startup Fleet seed fills the gaps; live telemetry overwrites as it arrives."""
    store = state.Store()
    store.seed(VIN, _prime(), tesla_id=999, display_name="DoodleMobile")   # seed at startup…
    # …then only a couple of live fields have streamed (parked car, on-change telemetry)
    store.ingest({"msg": "record_payload", "vin": VIN, "data": {"PackVoltage": 370.0, "Soc": 54.0}})
    f = api.state_payload(store)["vehicles"][0]["fields"]
    assert f["PackVoltage"]["source"] == "telemetry"             # live
    assert f["Soc"]["value"] == 54.0 and f["Soc"]["source"] == "telemetry"   # live overwrote the seed
    assert f["Odometer"]["source"] == "fleet"                    # filled from the seed (not streamed)
    assert f["RatedRange"]["source"] == "fleet" and f["RatedRange"]["value"] is not None
    assert "Gear" not in f and "VehicleSpeed" not in f           # ephemerals NEVER from the seed


def test_fields_view_seed_and_override_rules():
    store = state.Store()
    store.seed(VIN, {"drive_state": {"shift_state": "D", "speed": 40,
                                     "latitude": 47.4, "longitude": -122.2},
                     "charge_state": {"battery_level": 60}}, display_name="X")
    # seed-only: location filled, but gear/speed excluded as ephemeral (LIVE_ONLY)
    fv = store.fields_view(VIN)
    assert fv["Soc"]["value"] == 60 and fv["Soc"]["source"] == "fleet"
    assert "Location" in fv and "Gear" not in fv and "VehicleSpeed" not in fv
    # live telemetry overwrites the seed for the same field
    store.ingest({"msg": "record_payload", "vin": VIN, "data": {"Soc": 58.0}})
    fv = store.fields_view(VIN)
    assert fv["Soc"]["value"] == 58.0 and fv["Soc"]["source"] == "telemetry"
