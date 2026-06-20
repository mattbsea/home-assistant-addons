"""Phase 3 — the streaming sink driven by the Store event bus (not its own tail)."""
import asyncio
import importlib
import json

import websockets

state = importlib.import_module("app.state")
stream = importlib.import_module("app.sinks.stream")

VIN = "7SAYGDEE3PF884783"


class _RecWS:
    """A minimal stand-in websocket that records the messages the sink sends it."""
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(json.loads(payload))


def _ingest(store, **data):
    store.ingest({"msg": "record_payload", "vin": VIN, "data": data})


async def test_stream_emulates_tesla_park_disconnect():
    """Behave like Tesla's stream: data:update only while driving; on drive->park send the final
    frame + data:error vehicle_disconnected; stay quiet while parked (TeslaMate uses that to leave
    streaming mode -> fast REST poll -> close the drive + detect the charge)."""
    store = state.Store()
    sink = stream.StreamSink(store)
    ws = _RecWS()
    sink.subs[VIN] = {ws}
    _ingest(store, Location={"latitude": 47.77, "longitude": -122.15}, Soc=50)

    # Driving frame -> a data:update with shift_state "D".
    _ingest(store, CreatedAt="2026-06-18T01:21:45Z", Gear="ShiftStateD", VehicleSpeed=30)
    await sink._broadcast(VIN, "2026-06-18T01:21:45Z")
    drive_updates = [m for m in ws.sent if m["msg_type"] == "data:update"]
    assert drive_updates and drive_updates[-1]["value"].split(",")[9] == "D"   # shift_state column
    ws.sent.clear()

    # Park transition -> final data:update (shift "") AND a vehicle_disconnected error.
    _ingest(store, CreatedAt="2026-06-18T01:30:00Z", Gear="<invalid>")
    await sink._broadcast(VIN, "2026-06-18T01:30:00Z")
    types = [m["msg_type"] for m in ws.sent]
    assert "data:update" in types and "data:error" in types
    err = next(m for m in ws.sent if m["msg_type"] == "data:error")
    assert err["error_type"] == "vehicle_disconnected" and err["tag"] == VIN
    ws.sent.clear()

    # Already parked (e.g. a charging frame) -> QUIET, like Tesla (no data:update).
    _ingest(store, PackVoltage=370)
    await sink._broadcast(VIN, "2026-06-18T01:30:30Z")
    assert ws.sent == []


def test_lv_from_snapshot_flattens_location_and_gear():
    lv = stream.lv_from_snapshot({"Location": {"latitude": 47.77, "longitude": -122.15},
                                  "Gear": "ShiftStateD", "Soc": 50})
    assert lv["Latitude"] == 47.77 and lv["Longitude"] == -122.15
    assert lv["Gear"] == "D" and lv["Soc"] == 50


class _FakeElevation:
    def elevation(self, lat, lon):
        return 137 if (lat, lon) == (47.77, -122.15) else None


async def test_stream_sink_injects_resolver_elevation():
    store = state.Store()
    sink = stream.StreamSink(store, elevation=_FakeElevation())
    run_task = asyncio.create_task(sink.run())
    await asyncio.sleep(0.05)
    async with websockets.serve(sink.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with websockets.connect(f"ws://127.0.0.1:{port}/streaming/") as client:
            await client.send(json.dumps({"msg_type": "data:subscribe_oauth", "tag": VIN}))
            await asyncio.wait_for(client.recv(), 2)   # control:hello
            for data in ({"Location": {"latitude": 47.77, "longitude": -122.15}},
                         {"VehicleSpeed": 30},
                         {"CreatedAt": "2026-06-18T01:21:45Z", "Gear": "ShiftStateD"}):
                store.ingest({"msg": "record_payload", "vin": VIN, "data": data})
            msg = None
            for _ in range(6):
                m = json.loads(await asyncio.wait_for(client.recv(), 2))
                if m.get("msg_type") == "data:update":
                    msg = m
                    break
            assert msg and msg["value"].split(",")[4] == "137"   # elevation column from the resolver
    run_task.cancel()


async def test_stream_sink_backfills_odometer_from_prime_at_drive_start():
    """Regression: at drive start the slow 60 s Odometer/RatedRange haven't streamed yet, so a
    live-only view emits a blank odometer — TeslaMate then anchors the drive's start position to a
    null odometer and records a null start_km → null trip distance. The merged snapshot backfills
    last-known values from the Fleet-API prime so the opening frame already carries them."""
    store = state.Store()
    store.seed(VIN, {"vehicle_state": {"odometer": 35670.1},
                          "charge_state": {"battery_range": 161.9},
                          "drive_state": {"latitude": 47.77, "longitude": -122.15}},
                    display_name="X")
    sink = stream.StreamSink(store)
    run_task = asyncio.create_task(sink.run())
    await asyncio.sleep(0.05)
    async with websockets.serve(sink.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with websockets.connect(f"ws://127.0.0.1:{port}/streaming/") as client:
            await client.send(json.dumps({"msg_type": "data:subscribe_oauth", "tag": VIN}))
            await asyncio.wait_for(client.recv(), 2)   # control:hello
            # First live frames of the drive: position + gear, but NO Odometer/RatedRange yet.
            for data in ({"Location": {"latitude": 47.77, "longitude": -122.15}},
                         {"CreatedAt": "2026-06-18T01:21:45Z", "Gear": "ShiftStateD"}):
                store.ingest({"msg": "record_payload", "vin": VIN, "data": data})
            msg = None
            for _ in range(6):
                m = json.loads(await asyncio.wait_for(client.recv(), 2))
                if m.get("msg_type") == "data:update":
                    msg = m
                    break
            p = msg["value"].split(",")
            assert p[2] == "35670.1"    # odometer column, backfilled from prime (was blank → null km)
            assert p[10] == "161.9"     # range column, likewise from prime
    run_task.cancel()


async def test_stream_sink_computes_drive_power_from_pack():
    """End-to-end: a driving frame's power column is computed from PackVoltage/PackCurrent (kW),
    not the absent 'Power' field (which left stream power dead at 0)."""
    store = state.Store()
    sink = stream.StreamSink(store)
    run_task = asyncio.create_task(sink.run())
    await asyncio.sleep(0.05)
    async with websockets.serve(sink.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with websockets.connect(f"ws://127.0.0.1:{port}/streaming/") as client:
            await client.send(json.dumps({"msg_type": "data:subscribe_oauth", "tag": VIN}))
            await asyncio.wait_for(client.recv(), 2)   # control:hello
            for data in ({"Location": {"latitude": 47.77, "longitude": -122.15}},
                         {"PackVoltage": 360.0, "PackCurrent": -38.6},
                         {"CreatedAt": "2026-06-18T01:21:45Z", "Gear": "ShiftStateD"}):
                store.ingest({"msg": "record_payload", "vin": VIN, "data": data})
            msg = None
            for _ in range(6):
                m = json.loads(await asyncio.wait_for(client.recv(), 2))
                if m.get("msg_type") == "data:update":
                    msg = m
                    break
            assert msg and msg["value"].split(",")[8] == "14"   # power column, computed kW
    run_task.cancel()


async def test_stream_sink_broadcasts_from_bus():
    store = state.Store()
    sink = stream.StreamSink(store)
    run_task = asyncio.create_task(sink.run())
    await asyncio.sleep(0.05)  # let run() subscribe to the bus
    async with websockets.serve(sink.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with websockets.connect(f"ws://127.0.0.1:{port}/streaming/") as client:
            await client.send(json.dumps({"msg_type": "data:subscribe_oauth", "tag": VIN}))
            hello = json.loads(await asyncio.wait_for(client.recv(), 2))
            assert hello["msg_type"] == "control:hello"

            for data in ({"Location": {"latitude": 47.77, "longitude": -122.15}},
                         {"VehicleSpeed": 30},
                         {"Soc": 51.8, "GpsHeading": 90.0},
                         {"CreatedAt": "2026-06-18T01:21:45Z", "Gear": "ShiftStateD"}):
                store.ingest({"msg": "record_payload", "vin": VIN, "data": data})

            msg = None
            for _ in range(6):
                m = json.loads(await asyncio.wait_for(client.recv(), 2))
                if m.get("msg_type") == "data:update":
                    msg = m
                    break
            assert msg and msg["tag"] == VIN
            p = msg["value"].split(",")
            assert (p[6], p[7], p[9]) == ("47.77", "-122.15", "D")
            assert p[1] == "30" and p[3] == "51"
    run_task.cancel()
