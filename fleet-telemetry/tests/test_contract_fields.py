"""The single field model (fields.py) — pins the transforms consolidated from the v0 modules:
enum-strip, gear map, location parse, the telemetry<->vehicle_data mapping, and the meta sets.
"""
import importlib

import conftest
import pytest

fields = importlib.import_module("fields")


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
    assert fields.strip_state(raw) == expected


def test_parse_location_dict_string_none():
    assert fields.parse_location({"latitude": 47.77, "longitude": -122.15}) == (47.77, -122.15)
    assert fields.parse_location("47.77,-122.15") == (47.77, -122.15)
    assert fields.parse_location("nope") == (None, None)
    assert fields.parse_location(None) == (None, None)


@pytest.mark.parametrize("raw,expected", [
    ("ShiftStateD", "D"), ("ShiftStateP", "P"), ("ShiftStateR", "R"), ("ShiftStateN", "N"),
    ("P", "P"), ("Drive", "D"), ("Park", "P"), ("Reverse", "R"), ("Neutral", "N"),
])
def test_gear_letter(raw, expected):
    assert fields.gear_letter(raw) == expected


def test_prime_to_fields_mapping():
    response = conftest.load_reference("shim_vehicle_data.json")["response"]
    out = fields.prime_to_fields(response)
    assert out["Soc"] == 52                      # charge_state.battery_level
    assert out["BatteryLevel"] == 52             # charge_state.usable_battery_level
    assert out["DetailedChargeState"] == "Disconnected"
    assert out["Location"] == {"latitude": 47.768839, "longitude": -122.155053}
    assert out["VehicleName"] == "DoodleMobile"
    assert out["Odometer"] == 35595.12119278515
    assert out["CabinOverheatProtectionMode"] == "FanOnly"
    assert out["Gear"] == "D"                    # drive_state.shift_state
    assert out["ChargeRateMilePerHour"] == 0.0   # charge_state.charge_rate
    # active-route trip fields now reverse-mapped (panel finding A3/A5)
    assert out["DestinationName"] == "Home"      # active_route_destination
    assert out["DestinationLocation"] == {"latitude": 47.768694, "longitude": -122.14294}
    assert out["RouteTrafficMinutesDelay"] == 0.0
    assert out["ExpectedEnergyPercentAtTripArrival"] == 49


def test_gui_settings_reverse_mapped():
    out = fields.prime_to_fields({"gui_settings": {"gui_temperature_units": "F", "gui_distance_units": "mi/hr"}})
    assert out["SettingTemperatureUnit"] == "F"
    assert out["SettingDistanceUnit"] == "mi/hr"


def test_software_update_reverse_mapped_from_prime():
    """vehicle_state.software_update is in the Fleet-API prime, so the software fields populate on
    start (not only from the live stream)."""
    out = fields.prime_to_fields({"vehicle_state": {"software_update":
                                  {"version": "2026.20.1", "install_perc": 30, "download_perc": 100}}})
    assert out["SoftwareUpdateVersion"] == "2026.20.1"
    assert out["SoftwareUpdateInstallationPercentComplete"] == 30
    assert out["SoftwareUpdateDownloadPercentComplete"] == 100


def test_roster_additions_and_intervals():
    r = fields.TELEMETRY_FIELDS
    for name in ("EnergyRemaining", "SoftwareUpdateVersion", "SoftwareUpdateInstallationPercentComplete",
                 "SoftwareUpdateDownloadPercentComplete", "TpmsHardWarnings", "TpmsSoftWarnings"):
        assert name in r, name
    assert r["Location"]["interval_seconds"] == 10          # tightened for usable drive traces


def test_telemetry_fields_hash_stable_and_sensitive():
    h = fields.telemetry_fields_hash()
    assert isinstance(h, str) and len(h) == 64
    assert fields.telemetry_fields_hash() == h              # stable across calls
    saved = dict(fields.TELEMETRY_FIELDS)
    try:
        fields.TELEMETRY_FIELDS = dict(saved, ZzzNewField={"interval_seconds": 99})
        assert fields.telemetry_fields_hash() != h          # changes when roster changes
    finally:
        fields.TELEMETRY_FIELDS = saved


def test_meta_sets():
    # base set (dashboard/shim ingest keep connectivity keys via base); shim drops the extra three.
    assert fields.META_BASE == {"CreatedAt", "IsResend", "Vin"}
    assert fields.META_SHIM == fields.META_BASE | {"ConnectionID", "NetworkInterface", "Status"}


def test_enum_decoders():
    assert fields.fan_speed("HvacFanStatusSpeed3") == 3 and fields.fan_speed(0) == 0
    assert fields.window_state("WindowStateClosed") == 0 and fields.window_state("WindowStateOpen") == 2
    assert fields.defrost_on("DefrostModeOff") is False and fields.defrost_on("DefrostModeNormal") is True
    assert fields.round_int(51.6) == 52 and fields.round_int(None) is None
    assert fields.num("3.5") == 3.5 and fields.num("x") is None
