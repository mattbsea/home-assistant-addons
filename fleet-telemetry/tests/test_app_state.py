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
    assert snap["TpmsPressureFl"] == "<invalid>"
    # base meta dropped; connectivity frame kept (dashboard renders it)
    for meta in ("Vin", "CreatedAt", "IsResend"):
        assert meta not in snap
    assert snap["ConnectionID"] == "edeeafc2-d3d0-429f-8c43-254da435131c"
    assert snap["NetworkInterface"] == "cellular"
    assert store.total_records == len(conftest.load_records())


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
