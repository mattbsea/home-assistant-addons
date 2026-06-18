"""Phase 0 characterization — pins the CURRENT (v0.10.16) behavior of the field transforms that
the v1 refactor will pull into a shared fields.py. These must stay green through Phase 1.

Covers the duplicated logic the architecture review flagged: enum-strip, gear map, location parse,
the telemetry<->vehicle_data mapping, the records ingest, and the _META set.
"""
import importlib

import conftest
import pytest

server = importlib.import_module("server")
shim = importlib.import_module("shim")
bridge = importlib.import_module("bridge")


# --- enum stripping (shim._strip_state) -------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("ShiftStateD", "D"),
    ("HvacPowerStateOn", "On"),
    ("DetailedChargeStateDisconnected", "Disconnected"),
    ("Disconnected", "Disconnected"),   # no "State" marker -> unchanged
    ("<invalid>", None),
    ("", None),
    (None, None),
])
def test_strip_state(raw, expected):
    assert shim._strip_state(raw) == expected


# --- location parse ---------------------------------------------------------------------
def test_shim_parse_loc_dict():
    assert shim._parse_loc({"latitude": 47.77, "longitude": -122.15}) == (47.77, -122.15)


def test_shim_parse_loc_non_dict():
    assert shim._parse_loc("nope") == (None, None)


def test_server_parse_location_dict_and_string():
    assert server._parse_location({"latitude": 47.77, "longitude": -122.15}) == (47.77, -122.15)
    assert server._parse_location("47.77,-122.15") == (47.77, -122.15)
    assert server._parse_location(None) == (None, None)


# --- gear letter (bridge._gear_letter) --------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("ShiftStateD", "D"), ("ShiftStateP", "P"), ("ShiftStateR", "R"), ("ShiftStateN", "N"),
    ("P", "P"), ("Drive", "D"), ("Park", "P"), ("Reverse", "R"), ("Neutral", "N"),
])
def test_gear_letter(raw, expected):
    assert bridge._gear_letter(raw) == expected


# --- bridge.to_payload: telemetry record -> protojson Payload ---------------------------
def test_to_payload_value_typing_and_meta_exclusion():
    rec = {"vin": "7SAYGDEE3PF884783", "data": {
        "CreatedAt": "2026-06-18T01:21:13Z", "IsResend": False, "Vin": "7SAYGDEE3PF884783",
        "Soc": 51.85, "Locked": True, "Gear": "ShiftStateD",
        "Location": {"latitude": 47.77, "longitude": -122.15},
    }}
    out = bridge.to_payload(rec)
    assert out["vin"] == "7SAYGDEE3PF884783"
    items = {i["key"]: i["value"] for i in out["data"]}
    # _META keys never forwarded
    assert "Vin" not in items and "CreatedAt" not in items and "IsResend" not in items
    assert items["Soc"] == {"doubleValue": 51.85}
    assert items["Locked"] == {"booleanValue": True}
    assert items["Gear"] == {"shiftStateValue": "ShiftStateD"}
    assert items["Location"] == {"locationValue": {"latitude": 47.77, "longitude": -122.15}}


# --- server._prime_to_fields: vehicle_data (REST) -> telemetry field names --------------
def test_prime_to_fields_mapping():
    response = conftest.load_reference("shim_vehicle_data.json")["response"]
    out = server._prime_to_fields(response)
    assert out["Soc"] == 52                      # charge_state.battery_level
    assert out["BatteryLevel"] == 52             # charge_state.usable_battery_level
    assert out["DetailedChargeState"] == "Disconnected"
    assert out["Location"] == {"latitude": 47.768839, "longitude": -122.155053}
    assert out["VehicleName"] == "DoodleMobile"
    assert out["Odometer"] == 35595.12119278515
    assert out["CabinOverheatProtectionMode"] == "FanOnly"
    assert out["Gear"] == "D"                    # drive_state.shift_state
    assert out["ChargeRateMilePerHour"] == 0.0   # charge_state.charge_rate


# --- server._ingest: records -> per-VIN latest ------------------------------------------
def test_ingest_builds_latest_and_skips_meta():
    server._latest.clear()
    for obj in conftest.load_records():
        server._ingest(obj)
    latest = server._latest["7SAYGDEE3PF884783"]
    assert latest["Soc"]["value"] == 51.85185185185185
    assert latest["DoorState"]["value"]["TrunkRear"] is False
    assert latest["TpmsPressureFl"]["value"] == "<invalid>"
    # The server drops only Vin/CreatedAt/IsResend...
    for meta in ("Vin", "CreatedAt", "IsResend"):
        assert meta not in latest
    # ...and intentionally KEEPS the connectivity-frame keys — they feed the dashboard's
    # Network / Status rows. (The shim drops these; see test_meta_sets_diverge.)
    assert latest["ConnectionID"]["value"] == "edeeafc2-d3d0-429f-8c43-254da435131c"
    assert latest["NetworkInterface"]["value"] == "cellular"
    assert latest["Status"]["value"] == "CONNECTED"


# --- the duplicated _META sets (dedup target) -------------------------------------------
def test_meta_sets_diverge():
    # Pins the real, intentional divergence the v1 single fields module must preserve:
    # server + bridge share the base set; the shim additionally drops connectivity-frame keys.
    assert server._META == bridge._META == {"CreatedAt", "IsResend", "Vin"}
    assert shim._META == server._META | {"ConnectionID", "NetworkInterface", "Status"}
