"""Phase 3 — the unified app's state store + event bus.

Reuses the Phase 0 records fixture; the ingest behavior is pinned to match the v0 dashboard
(everything except base meta is stored, including connectivity keys; Soc/VehicleSpeed history).
"""
import asyncio
import importlib
import time

import conftest

state = importlib.import_module("app.state")

VIN = "7SAYGDEE3PF884783"


def _data(**d):
    return {"msg": "record_payload", "vin": VIN, "data": d}


def _conn(status):
    return {"msg": "record_payload", "vin": VIN, "metadata": {"txtype": "connectivity"},
            "data": {"Status": status, "ConnectionID": "c1", "NetworkInterface": "cellular", "Vin": VIN}}


def test_disconnected_enqueues_sleep_check():
    store = state.Store()
    store.ingest(_data(Soc=50, Location={"latitude": 47.4, "longitude": -122.2}))
    assert store.vehicle_state(VIN) == "online"          # streaming data + ready
    store.ingest(_conn("DISCONNECTED"))
    assert store.vehicles[VIN]["connected"] is False
    vin, epoch = store.sleep_checks.get_nowait()
    assert vin == VIN and epoch > 0
    # a DISCONNECTED alone does NOT immediately flip to asleep (settle + /products confirm does)
    assert store.vehicle_state(VIN) == "online"


def test_sleep_state_reported_and_cleared_on_reconnect():
    store = state.Store()
    store.ingest(_data(Soc=50, Location={"latitude": 47.4, "longitude": -122.2}))
    store.set_sleep_state(VIN, "asleep")
    assert store.vehicle_state(VIN) == "asleep"          # confirmed sleep wins
    store.ingest(_conn("CONNECTED"))                      # reconnect clears it
    assert store.vehicle_state(VIN) == "online"


def test_data_record_clears_sleep_state():
    store = state.Store()
    store.ingest(_data(Soc=50, Location={"latitude": 47.4, "longitude": -122.2}))
    store.set_sleep_state(VIN, "asleep")
    store.ingest(_data(Soc=51))                           # real telemetry = awake
    assert store.vehicle_state(VIN) == "online"


def test_reconnected_since():
    store = state.Store()
    store.ingest(_data(Soc=50))
    t = store.vehicles[VIN]["last_data_epoch"]
    assert store.reconnected_since(VIN, t - 1) is True
    assert store.reconnected_since(VIN, t + 1) is False


def test_vehicle_state_staleness_backstop():
    store = state.Store()
    store.ingest(_data(Soc=50, Location={"latitude": 47.4, "longitude": -122.2}))
    assert store.vehicle_state(VIN) == "online"
    store.vehicles[VIN]["last_data_epoch"] = time.time() - (state.ONLINE_WINDOW + 60)
    assert store.vehicle_state(VIN) == "asleep"           # no /products confirm, but stale -> backstop


def test_connectivity_frame_does_not_count_as_fresh_data():
    store = state.Store()
    store.ingest(_data(Soc=50, Location={"latitude": 47.4, "longitude": -122.2}))
    store.vehicles[VIN]["last_data_epoch"] = time.time() - (state.ONLINE_WINDOW + 60)   # data went stale
    store.ingest(_conn("DISCONNECTED"))                  # a connectivity frame must NOT refresh liveness
    assert store.vehicle_state(VIN) == "asleep"


def test_ingest_matches_v0_field_keeping():
    store = state.Store()
    for obj in conftest.load_records():
        store.ingest(obj)
    snap = store.snapshot(VIN)
    assert snap["Soc"] == 51.85185185185185
    assert snap["DoorState"]["TrunkRear"] is False
    assert "TpmsPressureFl" not in snap   # "<invalid>" sentinel is not stored (would clobber good seed/prior)
    # base meta dropped; connectivity frame kept (dashboard renders it)
    for meta in ("Vin", "CreatedAt", "IsResend"):
        assert meta not in snap
    assert snap["ConnectionID"] == "edeeafc2-d3d0-429f-8c43-254da435131c"
    assert snap["NetworkInterface"] == "cellular"
    assert store.total_records == len(conftest.load_records())


def test_charge_start_signal_fires_once_on_transition():
    store = state.Store()
    store.ingest({"msg": "record_payload", "vin": VIN, "data": {"Soc": 50}})   # not charging
    assert store.charge_starts.empty()
    store.ingest({"msg": "record_payload", "vin": VIN,
                  "data": {"DetailedChargeState": "DetailedChargeStateCharging", "DCChargingEnergyIn": 1.0}})
    assert store.charge_starts.get_nowait() == VIN          # signalled on the not-charging -> charging edge
    assert store.charge_starts.empty()
    store.ingest({"msg": "record_payload", "vin": VIN,
                  "data": {"DetailedChargeState": "DetailedChargeStateCharging", "Soc": 51}})
    assert store.charge_starts.empty()                      # still charging -> no new signal


def test_seed_keeps_online_via_fleet_epoch_when_stream_stale():
    store = state.Store()
    store.ingest(_data(Soc=50, Location={"latitude": 47.4, "longitude": -122.2}))
    store.vehicles[VIN]["last_data_epoch"] = time.time() - (state.ONLINE_WINDOW + 60)
    assert store.vehicle_state(VIN) == "asleep"   # stream stale, no fleet seed -> backstop
    store.seed(VIN, {"charge_state": {"battery_level": 60},
                     "drive_state": {"latitude": 47.4, "longitude": -122.2}})
    assert store.vehicle_state(VIN) == "online"   # a bridge seed keeps it online


def test_seed_clears_sleep_state():
    store = state.Store()
    store.ingest(_data(Soc=50, Location={"latitude": 47.4, "longitude": -122.2}))
    store.set_sleep_state(VIN, "asleep")
    store.seed(VIN, {"charge_state": {"battery_level": 60},
                     "drive_state": {"latitude": 47.4, "longitude": -122.2}})
    assert store.vehicle_state(VIN) == "online"   # a successful seed means the car is reachable


def test_streaming_excludes_fleet_seed():
    store = state.Store()
    store.seed(VIN, {"charge_state": {"battery_level": 60},
                     "drive_state": {"latitude": 47.4, "longitude": -122.2}})
    assert store.streaming(VIN, 90) is False       # a seed is NOT the live stream -> keep bridging
    store.ingest(_data(Soc=61))
    assert store.streaming(VIN, 90) is True         # real telemetry = streaming


def test_fleet_call_counter():
    store = state.Store()
    store.note_fleet_call("https://auth.tesla.com/oauth2/v3/token")
    store.note_fleet_call("https://fleet-api.prd.na.vn.cloud.tesla.com/api/1/products")
    store.note_fleet_call("https://fleet-api.prd.na.vn.cloud.tesla.com/api/1/vehicles/9/vehicle_data?endpoints=x")
    store.note_fleet_call("https://fleet-api.prd.na.vn.cloud.tesla.com/api/1/something_else")
    c = store.fleet_calls()
    assert c["total"] == 4 and c["token"] == 1 and c["products"] == 1 and c["vehicle_data"] == 1


def test_history_series_accumulate():
    store = state.Store()
    for obj in conftest.load_records():
        store.ingest(obj)
    v = store.vehicles[VIN]
    soc_vals = [val for _, val in v["history"]["soc"]]
    assert 51.85185185185185 in soc_vals
    assert all(isinstance(val, float) for _, val in v["history"]["speed"])


async def test_event_bus_publishes_changes():
    store = state.Store()
    loop = asyncio.get_running_loop()
    q = store.subscribe(loop)
    store.ingest({"msg": "record_payload", "vin": VIN, "data": {"Soc": 50.0, "Vin": VIN}})
    event = await asyncio.wait_for(q.get(), 2)
    assert event["vin"] == VIN
    assert event["changed"] == {"Soc": 50.0}
    store.unsubscribe(q)
    # after unsubscribe, no further delivery
    store.ingest({"msg": "record_payload", "vin": VIN, "data": {"Soc": 51.0}})
    assert q.empty()


def test_malformed_record_does_not_raise():
    """A non-record frame (data is a string/None/list) must be skipped, not crash the tail."""
    store = state.Store()
    for bad in ({"msg": "record_payload", "vin": VIN, "data": "oops"},
                {"msg": "record_payload", "vin": VIN, "data": ["a", "b"]},
                {"msg": "record_payload", "vin": VIN}):       # no data key
        store.ingest(bad)                                     # must not raise
    assert store.snapshot(VIN) == {}


def test_charge_baseline_captured_and_reset():
    store = state.Store()
    # Charging: baseline captured from DC energy-in at session start, then held steady.
    store.ingest({"msg": "record_payload", "vin": VIN,
                  "data": {"DetailedChargeState": "DetailedChargeStateCharging",
                           "DCChargingEnergyIn": 5.0}})
    assert store.charge_baseline(VIN) == 5.0
    store.ingest({"msg": "record_payload", "vin": VIN,
                  "data": {"DetailedChargeState": "DetailedChargeStateCharging",
                           "DCChargingEnergyIn": 8.0}})
    assert store.charge_baseline(VIN) == 5.0          # baseline frozen at session start
    # Disconnect clears it; an unknown VIN reads None.
    store.ingest({"msg": "record_payload", "vin": VIN,
                  "data": {"DetailedChargeState": "DetailedChargeStateDisconnected"}})
    assert store.charge_baseline(VIN) is None
    assert store.charge_baseline("NOPE") is None
