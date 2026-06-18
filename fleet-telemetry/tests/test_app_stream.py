"""Phase 3 — the streaming sink driven by the Store event bus (not its own tail)."""
import asyncio
import importlib
import json

import websockets

state = importlib.import_module("app.state")
stream = importlib.import_module("app.sinks.stream")

VIN = "7SAYGDEE3PF884783"


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
