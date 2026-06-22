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


def test_fleet_api_to_fields_mapping():
    response = conftest.load_reference("shim_vehicle_data.json")["response"]
    out = fields.fleet_api_to_fields(response)
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


@pytest.mark.parametrize("raw,expected", [
    ("CableTypeSAE", "SAE"),                          # live telemetry value strip_state missed
    ("FastChargerTypeSupercharger", "Supercharger"),
    ("DetailedChargeStateCharging", "Charging"),      # falls through to strip_state
    ("Disconnected", "Disconnected"),
    ("<invalid>", None), ("", None), (None, None),
])
def test_strip_enum(raw, expected):
    assert fields.strip_enum(raw) == expected


def test_gui_settings_reverse_mapped():
    out = fields.fleet_api_to_fields({"gui_settings": {"gui_temperature_units": "F", "gui_distance_units": "mi/hr"}})
    assert out["SettingTemperatureUnit"] == "F"
    assert out["SettingDistanceUnit"] == "mi/hr"


def test_software_update_reverse_mapped_from_prime():
    """vehicle_state.software_update is in the Fleet-API prime, so the software fields populate on
    start (not only from the live stream)."""
    out = fields.fleet_api_to_fields({"vehicle_state": {"software_update":
                                  {"version": "2026.20.1", "install_perc": 30, "download_perc": 100}}})
    assert out["SoftwareUpdateVersion"] == "2026.20.1"
    assert out["SoftwareUpdateInstallationPercentComplete"] == 30
    assert out["SoftwareUpdateDownloadPercentComplete"] == 100


def test_roster_additions_and_intervals():
    r = fields.TELEMETRY_FIELDS
    for name in ("EnergyRemaining", "SoftwareUpdateVersion", "SoftwareUpdateInstallationPercentComplete",
                 "SoftwareUpdateDownloadPercentComplete", "TpmsHardWarnings", "TpmsSoftWarnings"):
        assert name in r, name
    # drive-grade power + position: 1s sampling, on-change gated (minimum_delta) so it's dense while
    # driving (values swing) and near-silent when parked (values stable), plus a resend heartbeat.
    for f in ("PackVoltage", "PackCurrent", "Location"):
        assert r[f]["interval_seconds"] == 1, f
        assert r[f]["minimum_delta"] > 0 and r[f]["resend_interval_seconds"] > 0, f
    # tuned deltas: coarser than the initial defaults to cut charging chatter (current/voltage swing a
    # lot while charging, where TeslaMate uses ACChargingPower/DCChargingPower, not PackV*PackC).
    assert r["PackCurrent"]["minimum_delta"] == 0.5
    assert r["PackVoltage"]["minimum_delta"] == 1.0


def test_effective_roster_preserves_onchange_keys():
    # overriding only the interval keeps the field's default minimum_delta/resend_interval_seconds
    eff = fields.effective_roster({"PackCurrent": {"enabled": True, "interval_seconds": 2}})
    assert eff["PackCurrent"]["interval_seconds"] == 2
    assert eff["PackCurrent"]["minimum_delta"] == fields.TELEMETRY_FIELDS["PackCurrent"]["minimum_delta"]
    assert eff["PackCurrent"]["resend_interval_seconds"] == \
        fields.TELEMETRY_FIELDS["PackCurrent"]["resend_interval_seconds"]
    # explicit on-change overrides win
    eff2 = fields.effective_roster({"PackCurrent": {"enabled": True, "interval_seconds": 1,
                                                    "minimum_delta": 0.9, "resend_interval_seconds": 30}})
    assert eff2["PackCurrent"] == {"interval_seconds": 1, "minimum_delta": 0.9, "resend_interval_seconds": 30}


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


def test_effective_roster_overlays_override():
    # untouched default fields stay; an interval override applies; an essential disabled is dropped
    eff = fields.effective_roster({"VehicleSpeed": {"enabled": True, "interval_seconds": 3},
                                   "Gear": {"enabled": False}})
    assert eff["VehicleSpeed"] == {"interval_seconds": 3}
    assert "Gear" not in eff
    assert "Soc" in eff                                   # untouched default kept
    # adding a non-default (show-all) field when enabled
    eff2 = fields.effective_roster({"SeatHeaterLeft": {"enabled": True, "interval_seconds": 30}})
    assert eff2["SeatHeaterLeft"] == {"interval_seconds": 30}


def test_effective_roster_empty_is_default():
    assert fields.effective_roster({}) == fields.effective_roster(None) == dict(fields.TELEMETRY_FIELDS)


def test_hash_tracks_effective_roster():
    base = fields.telemetry_fields_hash(fields.effective_roster({}))
    changed = fields.telemetry_fields_hash(fields.effective_roster({"Soc": {"enabled": False}}))
    assert base != changed                               # disabling a field changes the fingerprint


def test_catalog_integrity():
    # every curated field has a group; every essential is curated; curated ⊆ the full proto enum
    allset = set(fields.ALL_FIELDS)
    for name in fields.TELEMETRY_FIELDS:
        assert name in fields.FIELD_GROUPS, f"{name} has no group"
        assert name in allset, f"{name} missing from ALL_FIELDS"
    for name in fields.ESSENTIAL_FIELDS:
        assert name in fields.TELEMETRY_FIELDS, f"essential {name} not in default roster"
    assert fields.PROFILES["teslamate"] == fields.TELEMETRY_FIELDS


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
