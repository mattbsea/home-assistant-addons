"""shim sink vehicle_data assembly — golden snapshot + behavior checks.

(The v0-equivalence test that proved the port was byte-identical did its job during migration; with
v0 removed we pin the assembled output to a committed golden instead.)
"""
import importlib
import json
import os

import conftest

state = importlib.import_module("app.state")
shim_data = importlib.import_module("app.sinks.shim_data")

VIN = "7SAYGDEE3PF884783"
PRIME_SECTIONS = ("drive_state", "charge_state", "climate_state", "vehicle_state", "vehicle_config")
IDENT = {"id": 1, "vehicle_id": 2, "vin": VIN, "display_name": "DoodleMobile", "in_service": False}
_GOLDEN = os.path.join(os.path.dirname(__file__), "fixtures", "golden", "shim_vehicle_data.golden.json")


def _prime():
    resp = conftest.load_reference("shim_vehicle_data.json")["response"]
    return {k: resp.get(k) for k in PRIME_SECTIONS}


def _assembled():
    store = state.Store()
    for r in conftest.load_records():
        store.ingest(r)
    return shim_data.vehicle_data(store.snapshot(VIN), ts=0, identity=IDENT,
                                  charge_baseline=None, prime=_prime())


def test_matches_golden():
    assert _assembled() == json.load(open(_GOLDEN))


def test_defaults_without_prime():
    store = state.Store()
    for r in conftest.load_records():
        store.ingest(r)
    vd = shim_data.vehicle_data(store.snapshot(VIN), ts=0, identity=IDENT)
    assert vd["charge_state"]["battery_level"] == 52
    assert vd["charge_state"]["charging_state"] == "Disconnected"
    assert vd["drive_state"]["shift_state"] == "D"
    assert vd["drive_state"]["power"] == 14           # round(-362.03 * -38.6 / 1000)
    assert vd["vehicle_state"]["odometer"] == 35595.12119278515
