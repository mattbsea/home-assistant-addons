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
