"""TeslaMate streaming-websocket sink for the unified app.

Subscribes to the Store event bus and pushes ``data:update`` frames to VIN-subscribed clients.
Reuses the verified pure builders in ws_stream; unlike the standalone ws_stream server it does NOT
tail the file itself — the single Store ingest feeds every sink.
"""
import asyncio
import json
import time

import websockets

import fields
import ws_stream


def lv_from_snapshot(snap):
    """Flatten a Store snapshot ({Field: value}) into the flat last-values dict the builder wants
    (Location split into Latitude/Longitude; Gear's enum prefix stripped)."""
    lv = dict(snap)
    lat, lon = fields.parse_location(snap.get("Location"))
    if lat is not None and lon is not None:
        lv["Latitude"], lv["Longitude"] = lat, lon
    g = snap.get("Gear")
    if isinstance(g, str):
        lv["Gear"] = fields.strip_state(g)
    return lv


class StreamSink:
    def __init__(self, store, elevation=None):
        self.store = store
        self.elevation = elevation   # elevation.Resolver or None — fills the stream elevation column
        self.subs = {}          # vin -> set[websocket]
        self.driving = {}       # vin -> bool: is the car currently driving (gates data:update, like Tesla)

    async def handler(self, ws):
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
                    if tag:
                        # Tesla answers a fresh subscribe with the current frame so the client's
                        # "real online" check (power is a number) passes even when parked. Re-sync the
                        # drive gate to the current shift so the next park edge fires vehicle_disconnected.
                        lv = lv_from_snapshot(self.store.snapshot(tag))
                        self.driving[tag] = lv.get("Gear") in ("D", "N", "R")
                        elev = self.elevation.elevation(lv.get("Latitude"), lv.get("Longitude")) if self.elevation else None
                        frame = ws_stream.build_data_update(tag, {tag: lv},
                                                            self.store.last_created_at(tag) or time.time(), elevation=elev)
                        if frame:
                            await ws.send(json.dumps(frame))
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

    async def _send(self, vin, messages):
        dead = set()
        for ws in list(self.subs.get(vin, ())):
            try:
                for m in messages:
                    await ws.send(json.dumps(m))
            except websockets.ConnectionClosed:
                dead.add(ws)
        for ws in dead:
            self.subs[vin].discard(ws)

    async def _broadcast(self, vin, created_at):
        if not self.subs.get(vin):
            return
        # The unified snapshot (telemetry overlaid on the Fleet seed): a drive's first frames land
        # before the slow 60 s Odometer/RatedRange have streamed, so the seed's last-known values keep
        # the opening frame from carrying a blank odometer (which made TeslaMate record null start_km →
        # null trip distance). Live always overrides once it streams.
        lv = lv_from_snapshot(self.store.snapshot(vin))
        driving = lv.get("Gear") in ("D", "N", "R")
        was_driving = self.driving.get(vin, False)
        # Behave exactly like Tesla's stream: data:update flows ONLY while driving; on the drive->park
        # edge send the final frame then data:error vehicle_disconnected; stay quiet while parked. That
        # quiet + disconnect is what makes TeslaMate leave streaming mode (streaming?=false -> fast REST
        # poll) and promptly close the drive + detect the charge. Continuously streaming parked/charging
        # frames (the old behavior) kept TeslaMate on its slow poll, so drives hung open.
        if not driving and not was_driving:
            return
        elev = self.elevation.elevation(lv.get("Latitude"), lv.get("Longitude")) if self.elevation else None
        update = ws_stream.build_data_update(vin, {vin: lv}, created_at, elevation=elev)
        if not update:
            return
        self.driving[vin] = driving
        messages = [update]
        if not driving and was_driving:   # drive -> park edge: Tesla sends vehicle_disconnected here
            messages.append({"msg_type": "data:error", "tag": vin, "error_type": "vehicle_disconnected"})
        await self._send(vin, messages)

    async def run(self):
        """Consume the Store bus forever, broadcasting a frame per change event."""
        q = self.store.subscribe(asyncio.get_running_loop())
        try:
            while True:
                event = await q.get()
                # Stamp the frame with the telemetry CreatedAt (sub-second), falling back to receive
                # time — whole-second timestamps wreck TeslaMate's regen-over-dt integral.
                await self._broadcast(event["vin"], event.get("created_at") or event.get("at"))
        finally:
            self.store.unsubscribe(q)
