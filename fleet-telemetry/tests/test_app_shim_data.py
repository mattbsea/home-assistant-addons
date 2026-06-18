"""Phase 3 — prove the unified app's shim sink produces byte-identical vehicle_data to the v0 shim.

Drives the same records + prime through both the legacy shim.Vehicle and the new
app.sinks.shim_data, and asserts equal output (ignoring the per-call timestamps).
"""
import importlib

import conftest

shim = importlib.import_module("shim")
state = importlib.import_module("app.state")
shim_data = importlib.import_module("app.sinks.shim_data")

VIN = "7SAYGDEE3PF884783"
PRIME_SECTIONS = ("drive_state", "charge_state", "climate_state", "vehicle_state", "vehicle_config")


def _strip_ts(o):
    if isinstance(o, dict):
        return {k: _strip_ts(v) for k, v in o.items() if k != "timestamp"}
    if isinstance(o, list):
        return [_strip_ts(x) for x in o]
    return o


def _prime():
    resp = conftest.load_reference("shim_vehicle_data.json")["response"]
    return {k: resp.get(k) for k in PRIME_SECTIONS}


def test_app_shim_data_matches_v0():
    recs = conftest.load_records()
    prime = _prime()

    veh = shim.Vehicle(VIN)
    for r in recs:
        veh.ingest(r["data"])
    veh.prime = prime
    vd0 = veh.vehicle_data()

    store = state.Store()
    for r in recs:
        store.ingest(r)
    vd1 = shim_data.vehicle_data(store.snapshot(VIN), ts=0, identity=veh.identity(),
                                 charge_baseline=veh.charge_baseline, prime=prime)

    assert _strip_ts(vd1) == _strip_ts(vd0)


def test_app_shim_data_defaults_without_prime():
    store = state.Store()
    for r in conftest.load_records():
        store.ingest(r)
    ident = {"id": 1, "vehicle_id": 2, "vin": VIN, "display_name": "DoodleMobile", "in_service": False}
    vd = shim_data.vehicle_data(store.snapshot(VIN), ts=0, identity=ident)
    # battery_level from Soc; charging defaults to Disconnected when no charge field is present.
    assert vd["charge_state"]["battery_level"] == 52        # round(51.85)
    assert vd["charge_state"]["charging_state"] == "Disconnected"
    # fixture has Gear=D -> driving -> power computed from pack V*I (= round(-362.03 * -38.6 / 1000)).
    assert vd["drive_state"]["shift_state"] == "D"
    assert vd["drive_state"]["power"] == 14
    assert vd["vehicle_state"]["odometer"] == 35595.12119278515
