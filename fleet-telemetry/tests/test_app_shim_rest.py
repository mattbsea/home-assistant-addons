"""Phase 3 — the Fleet-API shim served over HTTP from the unified Store."""
import importlib

import conftest
from starlette.testclient import TestClient

state = importlib.import_module("app.state")
shim_rest = importlib.import_module("app.sinks.shim_rest")

VIN = "7SAYGDEE3PF884783"


def _client():
    store = state.Store()
    for r in conftest.load_records():
        store.ingest(r)
    reg = shim_rest.Registry(store)
    return TestClient(shim_rest.build_app(store, reg)), reg


def test_products_and_identity():
    c, _ = _client()
    body = c.get("/api/1/products").json()
    assert body["count"] == 1
    ident = body["response"][0]
    assert ident["vin"] == VIN
    assert ident["state"] == "online"            # just-ingested, ready (Soc + Location)
    assert ident["id"] == shim_rest.synth_id(VIN, "id:")


def test_vehicle_data_over_http():
    c, _ = _client()
    eid = shim_rest.synth_id(VIN, "id:")
    vd = c.get(f"/api/1/vehicles/{eid}/vehicle_data").json()["response"]
    assert vd["vin"] == VIN
    assert vd["charge_state"]["battery_level"] == 52
    assert vd["charge_state"]["charging_state"] == "Disconnected"
    assert vd["vehicle_state"]["odometer"] == 35595.12119278515


def test_unknown_vehicle_404_and_token():
    c, _ = _client()
    assert c.get("/api/1/vehicles/999999/vehicle_data").status_code == 404
    tok = c.post("/oauth2/v3/token").json()
    assert tok["access_token"].startswith("qts-")


def test_charge_energy_added_uses_live_store_baseline():
    """Regression (#5): charge_energy_added must derive from the Store's live baseline, not the
    always-None Registry meta slot — so a charging vehicle reports energy added since session start."""
    store = state.Store()
    store.ingest({"msg": "record_payload", "vin": VIN,
                  "data": {"Soc": 50.0, "Location": {"latitude": 37.0, "longitude": -122.0},
                           "DetailedChargeState": "DetailedChargeStateCharging",
                           "DCChargingEnergyIn": 2.0}})
    store.ingest({"msg": "record_payload", "vin": VIN,
                  "data": {"DCChargingEnergyIn": 7.5}})       # +5.5 kWh since baseline
    reg = shim_rest.Registry(store)
    c = TestClient(shim_rest.build_app(store, reg))
    eid = shim_rest.synth_id(VIN, "id:")
    vd = c.get(f"/api/1/vehicles/{eid}/vehicle_data").json()["response"]
    assert vd["charge_state"]["charge_energy_added"] == 5.5


def test_not_ready_returns_408():
    store = state.Store()
    # a VIN seen only via a connectivity frame (no Soc/Location) is not "ready"
    store.ingest({"msg": "record_payload", "vin": VIN, "data": {"Status": "CONNECTED", "Vin": VIN}})
    reg = shim_rest.Registry(store)
    c = TestClient(shim_rest.build_app(store, reg))
    eid = shim_rest.synth_id(VIN, "id:")
    assert c.get(f"/api/1/vehicles/{eid}/vehicle_data").status_code == 408
