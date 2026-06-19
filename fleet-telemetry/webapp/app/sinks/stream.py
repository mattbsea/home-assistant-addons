"""TeslaMate streaming-websocket sink for the unified app.

Subscribes to the Store event bus and pushes ``data:update`` frames to VIN-subscribed clients.
Reuses the verified pure builders in ws_stream; unlike the standalone ws_stream server it does NOT
tail the file itself — the single Store ingest feeds every sink.
"""
import asyncio
import json

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

    async def _broadcast(self, vin, created_at):
        if not self.subs.get(vin):
            return
        # Merged (not live-only) snapshot: a drive's first frames land before the slow 60 s Odometer/
        # RatedRange fields have streamed, so a live-only view emits a blank odometer and TeslaMate
        # records a null start_km → null trip distance. The prime backfills last-known values so the
        # opening frame already carries them; live always overrides once it streams.
        lv = lv_from_snapshot(self.store.merged_snapshot(vin))
        elev = self.elevation.elevation(lv.get("Latitude"), lv.get("Longitude")) if self.elevation else None
        update = ws_stream.build_data_update(vin, {vin: lv}, created_at, elevation=elev)
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

    async def run(self):
        """Consume the Store bus forever, broadcasting a frame per change event."""
        q = self.store.subscribe(asyncio.get_running_loop())
        try:
            while True:
                event = await q.get()
                await self._broadcast(event["vin"], event.get("at"))
        finally:
            self.store.unsubscribe(q)
