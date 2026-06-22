"""Phase 2 — Python TeslaMate streaming ws server (replaces Node + bridge.py).

Pure transform tests plus a real end-to-end websocket test (no live system needed): start the
server, connect a client, subscribe by VIN, feed records, and assert the data:update frame.
"""
import asyncio
import importlib
import json

import websockets

ws_stream = importlib.import_module("ws_stream")

VIN = "7SAYGDEE3PF884783"


def rec(**data):
    return {"msg": "record_payload", "vin": VIN, "data": data}


def test_epoch_ms_preserves_subsecond():
    # ISO strings already keep ms; float epochs must too (whole-second truncation broke regen integrals)
    assert ws_stream._epoch_ms("2026-06-18T01:21:45.250Z") % 1000 == 250
    assert ws_stream._epoch_ms(1781974676.789) == 1781974676789


# --- pure: accumulate ----------------------------------------------------------------
def test_accumulate_location_gear_and_meta():
    last = {}                              # accumulate keys per-VIN
    ws_stream.accumulate(last, rec(Location={"latitude": 47.77, "longitude": -122.15}))
    ws_stream.accumulate(last, rec(Gear="ShiftStateD"))
    ws_stream.accumulate(last, rec(Vin=VIN, CreatedAt="x", Soc=51.8))
    lv = last[VIN]
    assert lv["Latitude"] == 47.77 and lv["Longitude"] == -122.15
    assert lv["Gear"] == "D"               # "ShiftState" prefix stripped
    assert lv["Soc"] == 51.8
    assert "Vin" not in lv and "CreatedAt" not in lv


# --- pure: build_data_update ---------------------------------------------------------
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
    STILL be emitted with shift_state='' so TeslaMate ends the drive (suppressing it stranded it)."""
    last = {}
    ws_stream.accumulate(last, rec(Location={"latitude": 47.77, "longitude": -122.15}))
    ws_stream.accumulate(last, rec(Gear="D"))
    ws_stream.accumulate(last, rec(Gear="<invalid>"))      # parked
    lv = last[VIN]
    assert lv["Gear"] is None and "Gear" in lv             # seen, then went None
    msg = ws_stream.build_data_update(VIN, {VIN: lv}, "2026-06-18T01:21:45Z")
    assert msg is not None                                 # NOT suppressed
    assert msg["value"].split(",")[9] == ""                # shift_state blank, not "None"/"D"


def test_park_blanks_stale_speed():
    """Regression: VehicleSpeed is on-change, so it stops streaming on park and lv retains the last
    driving value (e.g. 37). The parked frame must blank speed (-> TeslaMate's nil fallback clears
    sensor.tesla_speed) instead of reporting the stale 37, matching the REST shim's driving gate."""
    last = {}
    ws_stream.accumulate(last, rec(Location={"latitude": 47.77, "longitude": -122.15}))
    ws_stream.accumulate(last, rec(Gear="D", VehicleSpeed=37))
    # driving frame carries the speed
    msg = ws_stream.build_data_update(VIN, last, "2026-06-18T01:21:45Z")
    assert msg["value"].split(",")[1] == "37"
    ws_stream.accumulate(last, rec(Gear="<invalid>"))      # parked; VehicleSpeed unchanged -> stale 37
    assert last[VIN]["VehicleSpeed"] == 37                 # still retained in last-values
    msg = ws_stream.build_data_update(VIN, last, "2026-06-18T01:21:45Z")
    p = msg["value"].split(",")
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


# --- end-to-end over a real websocket ------------------------------------------------
async def test_end_to_end_subscribe_and_update():
    stream = ws_stream.Stream()
    stream.loop = asyncio.get_running_loop()
    async with websockets.serve(stream.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with websockets.connect(f"ws://127.0.0.1:{port}/streaming/") as client:
            await client.send(json.dumps({"msg_type": "data:subscribe_oauth", "tag": VIN, "token": ""}))
            hello = json.loads(await asyncio.wait_for(client.recv(), 2))
            assert hello["msg_type"] == "control:hello"

            # Build state; the Gear record completes the position+gear triad and triggers a frame.
            await stream.feed(rec(Location={"latitude": 47.77, "longitude": -122.15}))
            await stream.feed(rec(VehicleSpeed=30))
            await stream.feed(rec(Soc=51.8, GpsHeading=90.0))
            await stream.feed(rec(CreatedAt="2026-06-18T01:21:45Z", Gear="ShiftStateD"))

            msg = json.loads(await asyncio.wait_for(client.recv(), 2))
            assert msg["msg_type"] == "data:update" and msg["tag"] == VIN
            p = msg["value"].split(",")
            assert (p[6], p[7], p[9]) == ("47.77", "-122.15", "D")
            assert p[1] == "30" and p[3] == "51"
