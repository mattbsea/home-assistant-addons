"""Phase 3 — the unified app's state store + event bus.

Reuses the Phase 0 records fixture; the ingest behavior is pinned to match the v0 dashboard
(everything except base meta is stored, including connectivity keys; Soc/VehicleSpeed history).
"""
import asyncio
import importlib

import conftest

state = importlib.import_module("app.state")

VIN = "7SAYGDEE3PF884783"


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
