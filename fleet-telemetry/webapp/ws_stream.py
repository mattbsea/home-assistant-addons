#!/usr/bin/env python3
"""Python TeslaMate streaming-websocket server (replaces the bundled Node teslamate-ws + bridge.py).

TeslaMate's streaming client connects to ``ws://<host>:8081/streaming/`` and subscribes by VIN
(``data:subscribe_oauth``). This server tails the fleet-telemetry records file directly, accumulates
the latest per-VIN values, and pushes TeslaMate ``data:update`` frames to subscribers — no Google
Pub/Sub, no double JSON transform, no Node runtime.

The ``data:update`` value is a CSV in the exact column order TeslaMate's stream parser expects:
  time_ms, speed, odometer, soc, elevation, est_heading, est_lat, est_lng, power, shift_state,
  range, est_range, heading
A frame is only emitted once Latitude, Longitude and Gear have all been seen for the VIN (matches
the previous behavior).
"""
import asyncio
import json
import threading
from datetime import datetime

import websockets

import fields
import records


def _epoch_ms(created_at):
    if isinstance(created_at, (int, float)):
        return int(created_at) * 1000
    s = str(created_at).replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(s).timestamp() * 1000)
    except ValueError:
        return 0


def _int_or_blank(v):
    n = fields.num(v)
    return "" if n is None else int(n)


def _blank_if_none(v):
    return "" if v is None else v


def accumulate(last_values, rec):
    """Fold one record's data into the per-VIN last_values dict. Returns the VIN or None."""
    data = rec.get("data") or {}
    vin = rec.get("vin") or data.get("Vin")
    if not vin:
        return None
    lv = last_values.setdefault(vin, {})
    for key, value in data.items():
        if key in fields.META_BASE:
            continue
        if key == "Location":
            lat, lon = fields.parse_location(value)
            if lat is not None and lon is not None:
                lv["Latitude"], lv["Longitude"] = lat, lon
        elif key == "Gear":
            lv["Gear"] = fields.strip_state(value) if isinstance(value, str) else value
        else:
            lv[key] = value
    return vin


def build_data_update(vin, last_values, created_at):
    """Build a TeslaMate ``data:update`` message, or None if position/gear aren't known yet."""
    lv = last_values.get(vin, {})
    # Gear must have been *seen* at least once (key present) — but once seen it can be None, which is
    # how a park reads: Tesla streams Gear="<invalid>" on park, strip_state -> None. We must still emit
    # that frame (shift_state="") so TeslaMate sees the drive END; suppressing it strands the drive.
    if lv.get("Latitude") is None or lv.get("Longitude") is None or "Gear" not in lv:
        return None
    power = 0
    p = fields.num(lv.get("Power"))
    if p is not None:
        power = int(p)
    for src in ("DCChargingPower", "ACChargingPower"):
        cp = fields.num(lv.get(src))
        if cp is not None and cp > 0:
            power = int(cp)
    # VehicleSpeed is an on-change field: it stops streaming when the car parks, so lv retains the
    # last *driving* value indefinitely. Emitting it on a parked frame strands a phantom speed in
    # TeslaMate (summary.ex publishes mph_to_kmh(speed) whenever speed is non-nil, with NO shift gate
    # — only ""/nil clears it). Gate on driving, exactly like the REST shim's assemble(), so a parked
    # frame carries speed="" -> TeslaMate's nil fallback clears sensor.tesla_speed.
    speed = _int_or_blank(lv.get("VehicleSpeed")) if lv.get("Gear") in ("D", "R", "N") else ""
    value = ",".join(str(x) for x in [
        _epoch_ms(created_at),
        speed,
        _blank_if_none(lv.get("Odometer")),
        _int_or_blank(lv.get("Soc")),
        "",                                      # elevation (not provided)
        _blank_if_none(lv.get("GpsHeading")),    # est_heading
        lv["Latitude"],                          # est_lat
        lv["Longitude"],                         # est_lng
        power,
        _blank_if_none(lv.get("Gear")),          # shift_state ("" when parked / Gear=<invalid>)
        _blank_if_none(lv.get("RatedRange")),    # range
        _blank_if_none(lv.get("EstBatteryRange")),  # est_range
        _blank_if_none(lv.get("GpsHeading")),    # heading
    ])
    return {"msg_type": "data:update", "tag": vin, "value": value}


class Stream:
    """Accumulates per-VIN state and fans data:update frames out to subscribed websockets."""

    def __init__(self):
        self.subs = {}          # vin -> set[websocket]
        self.last = {}          # vin -> {field: value}
        self.loop = None

    async def handler(self, ws):
        """One TeslaMate connection. Registers on subscribe, keepalive-pings, cleans up on close."""
        tags = set()
        keepalive = asyncio.create_task(self._keepalive(ws))
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("msg_type") in ("data:subscribe_oauth", "data:subscribe_all"):
                    tag = msg.get("tag")
                    if tag:
                        self.subs.setdefault(tag, set()).add(ws)
                        tags.add(tag)
                    await ws.send(json.dumps({"msg_type": "control:hello", "connection_timeout": 30000}))
        except websockets.ConnectionClosed:
            pass
        finally:
            keepalive.cancel()
            for tag in tags:
                self.subs.get(tag, set()).discard(ws)

    async def _keepalive(self, ws):
        try:
            while True:
                await asyncio.sleep(10)
                await ws.send(json.dumps({"msg_type": "control:hello", "connection_timeout": 30000}))
        except (asyncio.CancelledError, websockets.ConnectionClosed):
            pass

    async def feed(self, rec):
        """Ingest one telemetry record and broadcast a data:update if one is producible."""
        if rec.get("msg") != "record_payload":
            return
        vin = accumulate(self.last, rec)
        if not vin or not self.subs.get(vin):
            return
        update = build_data_update(vin, self.last, (rec.get("data") or {}).get("CreatedAt"))
        if not update:
            return
        payload = json.dumps(update)
        dead = set()
        for ws in list(self.subs.get(vin, ())):
            try:
                await ws.send(payload)
            except websockets.ConnectionClosed:
                dead.add(ws)
        for ws in dead:
            self.subs[vin].discard(ws)

    def start_tail(self, path):
        """Background thread: follow the records file and schedule feed() on the event loop."""
        def run():
            for rec in records.tail(path):
                if self.loop is not None:
                    asyncio.run_coroutine_threadsafe(self.feed(rec), self.loop)
        threading.Thread(target=run, daemon=True).start()


async def main(records_file, port=8081):
    stream = Stream()
    stream.loop = asyncio.get_running_loop()
    stream.start_tail(records_file)
    async with websockets.serve(stream.handler, "0.0.0.0", port):
        print(f"[ws-stream] TeslaMate websocket server listening on :{port}/streaming/", flush=True)
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    import os
    asyncio.run(main(os.environ.get("FT_RECORDS_FILE", "/tmp/ft-records.jsonl"),
                     int(os.environ.get("FT_WS_PORT", "8081"))))
