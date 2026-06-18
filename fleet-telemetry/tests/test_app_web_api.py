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


def test_dashboard_shows_superset_from_prime_when_telemetry_sparse():
    """The fix: a freshly-restarted/parked car (almost nothing streamed yet) still shows a full
    dashboard, because the prime fills the gaps via the merged superset."""
    store = state.Store()
    # only a couple of live fields have streamed (parked car, on-change telemetry)
    store.ingest({"msg": "record_payload", "vin": VIN, "data": {"PackVoltage": 370.0, "Soc": 54.0}})
    store.set_prime(VIN, _prime(), tesla_id=999, display_name="DoodleMobile")
    f = api.state_payload(store)["vehicles"][0]["fields"]
    assert f["PackVoltage"]["source"] == "telemetry"             # live
    assert f["Soc"]["value"] == 54.0 and f["Soc"]["source"] == "telemetry"   # live wins over prime
    assert f["Odometer"]["source"] == "prime"                    # filled from prime (not streamed)
    assert f["RatedRange"]["source"] == "prime" and f["RatedRange"]["value"] is not None
    assert "Gear" not in f and "VehicleSpeed" not in f           # ephemerals NEVER from prime


def test_merged_fields_ephemeral_and_override_rules():
    store = state.Store()
    store.set_prime(VIN, {"drive_state": {"shift_state": "D", "speed": 40,
                                          "latitude": 47.4, "longitude": -122.2},
                          "charge_state": {"battery_level": 60}}, display_name="X")
    # prime-only: location filled, but gear/speed excluded as ephemeral
    mf = store.merged_fields(VIN)
    assert mf["Soc"]["value"] == 60 and mf["Soc"]["source"] == "prime"
    assert "Location" in mf and "Gear" not in mf and "VehicleSpeed" not in mf
    # live telemetry overrides prime for the same element
    store.ingest({"msg": "record_payload", "vin": VIN, "data": {"Soc": 58.0}})
    mf = store.merged_fields(VIN)
    assert mf["Soc"]["value"] == 58.0 and mf["Soc"]["source"] == "telemetry"
