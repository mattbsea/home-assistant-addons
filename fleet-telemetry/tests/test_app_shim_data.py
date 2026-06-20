"""shim sink vehicle_data assembly — golden snapshot + behavior checks.

Post-SSOT: the shim reads ONE unified snapshot (telemetry overlaid on the Fleet-API seed); there is
no read-time prime-merge. The golden was captured from the pre-SSOT code (assemble + raw prime
backfill); equivalence is asserted field-by-field, allowing only the inert raw-prime keys the old
backfill leaked but `assemble` never produced (TeslaMate ignores them).
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


def test_park_gear_assembles_null_shift_state():
    """The REST vehicle_data is what actually closes a drive (TeslaMate ends it when a fetch returns
    shift_state nil/P). Confirm park reads as None: Gear='<invalid>' (park) and 'ShiftStateP' -> None;
    only D/N/R stay set."""
    base = {"Soc": 50, "Location": {"latitude": 47.4, "longitude": -122.2}}
    for gear, expected in (("ShiftStateD", "D"), ("ShiftStateR", "R"),
                           ("ShiftStateP", None), ("<invalid>", None)):
        vd = shim_data.vehicle_data({**base, "Gear": gear}, ts=1, identity=IDENT)
        assert vd["drive_state"]["shift_state"] == expected, f"{gear} -> {expected}"
_GOLDEN = os.path.join(os.path.dirname(__file__), "fixtures", "golden", "shim_vehicle_data.golden.json")

# Raw Fleet-API keys the OLD backfill copied verbatim into the output but `assemble` never produces.
# TeslaMate doesn't persist any of these, so the SSOT (assemble-only) correctly drops them.
INERT_DROPPED = {
    "drive_state": {"gps_as_of", "native_latitude", "native_longitude",
                    "native_location_supported", "native_type"},
    "charge_state": {"charge_amps"},
    "climate_state": {"defrost_mode"},
}


def _prime():
    resp = conftest.load_reference("shim_vehicle_data.json")["response"]
    return {k: resp.get(k) for k in PRIME_SECTIONS}


def _assembled():
    store = state.Store()
    store.seed(VIN, _prime())                       # Fleet seed first…
    for r in conftest.load_records():               # …then live telemetry overwrites (last-writer-wins)
        store.ingest(r)
    return shim_data.vehicle_data(store.snapshot(VIN), ts=0, identity=IDENT, charge_baseline=None)


def test_matches_golden():
    """Equivalence vs the pre-SSOT golden: every key/value preserved except documented inert drops,
    and no surprise new keys."""
    got = _assembled()
    golden = json.load(open(_GOLDEN))
    for sec, gval in golden.items():
        if isinstance(gval, dict):
            inert = INERT_DROPPED.get(sec, set())
            for k, v in gval.items():
                if k in inert:
                    assert k not in got[sec], f"{sec}.{k} should be dropped (inert)"
                else:
                    assert got[sec].get(k) == v, f"{sec}.{k}: {got[sec].get(k)!r} != golden {v!r}"
            assert set(got[sec]) - set(gval) == set(), f"unexpected new keys in {sec}: {set(got[sec]) - set(gval)}"
        else:
            assert got[sec] == gval


def test_defaults_without_seed():
    store = state.Store()
    for r in conftest.load_records():
        store.ingest(r)
    vd = shim_data.vehicle_data(store.snapshot(VIN), ts=0, identity=IDENT)
    assert vd["charge_state"]["battery_level"] == 52
    assert vd["charge_state"]["charging_state"] == "Disconnected"
    assert vd["drive_state"]["shift_state"] == "D"
    assert vd["drive_state"]["power"] == 14           # round(-362.03 * -38.6 / 1000)
    assert vd["vehicle_state"]["odometer"] == 35595.12119278515


def _rec(**data):
    return {"msg": "record_payload", "vin": VIN, "data": data}


def test_live_charge_rate_wins_over_stale_seed():
    """Live telemetry overwrites the Fleet seed (last-writer-wins): a live charge rate beats the
    seed's value rather than being masked by it."""
    store = state.Store()
    store.seed(VIN, {"charge_state": {"charge_rate": 0.0, "charge_port_latch": "Disengaged"},
                     "climate_state": {"cabin_overheat_protection": "FanOnly"},
                     "vehicle_state": {"vehicle_name": "OldName"}})
    store.ingest(_rec(Soc=50, Location={"latitude": 47.4, "longitude": -122.2},
                      ChargeRateMilePerHour=25.0, ChargePortLatch="Engaged",
                      CabinOverheatProtectionMode="CabinOverheatProtectionModeStateOff",
                      VehicleName="DoodleMobile"))
    vd = shim_data.vehicle_data(store.snapshot(VIN), ts=0, identity=IDENT)
    assert vd["charge_state"]["charge_rate"] == 25.0          # live, not stale 0.0
    assert vd["charge_state"]["charge_port_latch"] == "Engaged"
    assert vd["climate_state"]["cabin_overheat_protection"] == "Off"
    assert vd["vehicle_state"]["vehicle_name"] == "DoodleMobile"


def test_parked_speed_is_zero_not_null_to_clear_mqtt_sensor():
    """TeslaMate's MQTT publisher skips a nil 'speed' (speed is not in @publish_if_nil) on a *retained*
    topic, so a null parked speed leaves sensor.tesla_speed stuck at the last driving value. The REST
    shim must report 0 when parked (not None) so TeslaMate publishes 0 and the sensor clears. The stale
    streamed VehicleSpeed must NOT leak through — parked is always 0."""
    base = {"Soc": 50, "Location": {"latitude": 47.4, "longitude": -122.2}}
    parked = shim_data.vehicle_data({**base, "Gear": "ShiftStateP", "VehicleSpeed": 5}, ts=1, identity=IDENT)
    assert parked["drive_state"]["speed"] == 0          # parked -> 0 clears the retained MQTT sensor
    driving = shim_data.vehicle_data({**base, "Gear": "ShiftStateD", "VehicleSpeed": 37}, ts=1, identity=IDENT)
    assert driving["drive_state"]["speed"] == 37         # driving -> real speed


def test_seed_never_supplies_ephemeral_gear_speed():
    """On park, live telemetry has shift_state=None and speed reported as 0 (see above). The Fleet seed
    (LIVE_ONLY-skipped) must never supply gear/speed, or the shim keeps reporting 'driving at 38' after
    parking. Non-ephemeral seed fields (heading) still show through until telemetry overrides."""
    store = state.Store()
    store.seed(VIN, {"drive_state": {"shift_state": "D", "speed": 38, "heading": 200}})
    store.ingest(_rec(Soc=54, Location={"latitude": 47.4, "longitude": -122.2},
                      Gear="<invalid>", VehicleSpeed="<invalid>"))
    vd = shim_data.vehicle_data(store.snapshot(VIN), ts=0, identity=IDENT)
    assert vd["drive_state"]["shift_state"] is None    # parked, not stale "D"
    assert vd["drive_state"]["speed"] == 0              # parked -> 0, not stale 38, not null
    assert vd["drive_state"]["heading"] == 200          # non-ephemeral seed field shows through
