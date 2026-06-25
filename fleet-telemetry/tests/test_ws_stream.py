"""Pure transform tests for the TeslaMate ``data:update`` builders (ws_stream).

The live ws server (StreamSink) is covered end-to-end in test_app_stream.py; here we only pin the
stateless CSV-frame transforms it reuses.
"""
import importlib

ws_stream = importlib.import_module("ws_stream")

VIN = "7SAYGDEE3PF884783"


def test_epoch_ms_preserves_subsecond():
    # ISO strings already keep ms; float epochs must too (whole-second truncation broke regen integrals)
    assert ws_stream._epoch_ms("2026-06-18T01:21:45.250Z") % 1000 == 250
    assert ws_stream._epoch_ms(1781974676.789) == 1781974676789


def test_build_update_needs_position_and_gear():
    lv = {"Soc": 50}
    assert ws_stream.build_data_update(VIN, {VIN: lv}, "2026-06-18T01:21:45Z") is None


def test_build_update_csv_columns():
    lv = {"Latitude": 47.77, "Longitude": -122.15, "Gear": "D",
          "VehicleSpeed": 30, "Soc": 51.8, "GpsHeading": 90.0}
    msg = ws_stream.build_data_update(VIN, {VIN: lv}, "2026-06-18T01:21:45Z")
    assert msg["msg_type"] == "data:update" and msg["tag"] == VIN
    p = msg["value"].split(",")
    assert p[1] == "30"          # speed (int)
    assert p[3] == "51"          # soc (int)
    assert p[5] == "90.0"        # est_heading
    assert p[6] == "47.77"       # est_lat
    assert p[7] == "-122.15"     # est_lng
    assert p[9] == "D"           # shift_state
    assert int(p[0]) > 0         # time_ms parsed from ISO CreatedAt


def test_elevation_column_filled_and_blank():
    """Column 4 (elevation) is blank by default (Fleet API/Telemetry omit elevation) and carries the
    resolver-provided meters when supplied."""
    lv = {"Latitude": 47.77, "Longitude": -122.15, "Gear": "D", "VehicleSpeed": 30}
    blank = ws_stream.build_data_update(VIN, {VIN: lv}, "2026-06-18T01:21:45Z")
    assert blank["value"].split(",")[4] == ""              # unresolved -> blank, not "None"
    filled = ws_stream.build_data_update(VIN, {VIN: lv}, "2026-06-18T01:21:45Z", elevation=123.6)
    assert filled["value"].split(",")[4] == "123"          # rounded int meters


def test_park_emits_blank_shift_state():
    """Regression: on park Tesla streams Gear='<invalid>' -> strip_state -> None. The frame must
    STILL be emitted with shift_state='' so TeslaMate ends the drive (suppressing it stranded it).
    The 'Gear' key is present (seen) but its value is None (parked)."""
    lv = {"Latitude": 47.77, "Longitude": -122.15, "Gear": None}
    msg = ws_stream.build_data_update(VIN, {VIN: lv}, "2026-06-18T01:21:45Z")
    assert msg is not None                                 # NOT suppressed
    assert msg["value"].split(",")[9] == ""                # shift_state blank, not "None"/"D"


def test_park_blanks_stale_speed():
    """Regression: VehicleSpeed is on-change, so it stops streaming on park and lv retains the last
    driving value (e.g. 37). The parked frame must blank speed (-> TeslaMate's nil fallback clears
    sensor.tesla_speed) instead of reporting the stale 37, matching the REST shim's driving gate."""
    base = {"Latitude": 47.77, "Longitude": -122.15, "VehicleSpeed": 37}
    driving = ws_stream.build_data_update(VIN, {VIN: dict(base, Gear="D")}, "2026-06-18T01:21:45Z")
    assert driving["value"].split(",")[1] == "37"          # driving -> speed carried
    parked = ws_stream.build_data_update(VIN, {VIN: dict(base, Gear=None)}, "2026-06-18T01:21:45Z")
    p = parked["value"].split(",")
    assert p[9] == "" and p[1] == ""                       # shift_state blank AND speed blank, not 37


def test_power_computed_from_pack_voltage_current_when_driving():
    """Regression: there is no streamed 'Power' field, so the power column was always 0. It must be
    computed as -PackVoltage*PackCurrent/1000 (kW) while driving, like the REST shim. Discharge
    (PackCurrent<0) -> positive drive power; regen (PackCurrent>0) -> negative."""
    base = {"Latitude": 47.77, "Longitude": -122.15}
    drive = ws_stream.build_data_update(VIN, {VIN: dict(base, Gear="D", PackVoltage=360.0, PackCurrent=-38.6)},
                                        "2026-06-18T01:21:45Z")
    assert drive["value"].split(",")[8] == "14"     # -360 * -38.6 / 1000 = 13.9 -> 14 kW
    regen = ws_stream.build_data_update(VIN, {VIN: dict(base, Gear="D", PackVoltage=360.0, PackCurrent=15.5)},
                                        "2026-06-18T01:21:45Z")
    assert regen["value"].split(",")[8] == "-6"     # -360 * 15.5 / 1000 = -5.6 -> -6 kW (regen)


def test_power_zero_when_parked_even_with_pack_data():
    """Parked (Gear None/<invalid>) -> power 0, never the idle PackVoltage*PackCurrent draw."""
    lv = {"Latitude": 47.77, "Longitude": -122.15, "Gear": None, "PackVoltage": 360.0, "PackCurrent": -1.0}
    msg = ws_stream.build_data_update(VIN, {VIN: lv}, "2026-06-18T01:21:45Z")
    assert msg["value"].split(",")[8] == "0"


def test_build_update_suppressed_until_gear_seen():
    """Before Gear is ever seen, suppress (can't tell driving from parked yet)."""
    lv = {"Latitude": 47.77, "Longitude": -122.15, "Soc": 50}   # no Gear key
    assert ws_stream.build_data_update(VIN, {VIN: lv}, "2026-06-18T01:21:45Z") is None
