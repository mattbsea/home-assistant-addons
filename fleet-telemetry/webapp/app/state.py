"""Per-VIN telemetry state + async pub/sub event bus — the heart of the v1 app.

A single records reader calls `Store.ingest(record)`; the store updates the per-VIN field map and
short history series, then publishes a change event to every subscriber queue. Sinks (dashboard
SSE, TeslaMate shim, streaming ws, MQTT) subscribe to the bus instead of each tailing the file.

Field-keeping matches the v0 dashboard exactly: everything except the base meta keys (CreatedAt /
IsResend / Vin) is stored — including the connectivity-frame keys the dashboard renders.
"""
import asyncio
import threading
import time
from collections import deque

import fields

HISTORY_MAX = 600  # samples kept per sparkline series

# Ephemeral drive fields are LIVE-or-nothing: a prime snapshot can be up to a re-prime interval
# (30 min) stale, so we never let it supply gear/speed — that once stranded TeslaMate "driving".
PRIME_EPHEMERAL_FIELDS = ("Gear", "VehicleSpeed")


class Store:
    def __init__(self):
        self._lock = threading.Lock()
        self.vehicles = {}            # vin -> {fields, history, last_epoch, display_name}
        self.total_records = 0
        self.last_record_epoch = 0.0
        self._record_times = deque(maxlen=5000)   # epochs, for the records/min stat
        self._subscribers = []        # list[(loop, asyncio.Queue)]

    # --- subscription (event bus) ------------------------------------------------------
    def subscribe(self, loop):
        q = asyncio.Queue()
        with self._lock:
            self._subscribers.append((loop, q))
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subscribers = [(l, qq) for (l, qq) in self._subscribers if qq is not q]

    def _publish(self, event):
        for loop, q in list(self._subscribers):
            try:
                loop.call_soon_threadsafe(q.put_nowait, event)
            except RuntimeError:
                pass  # loop stopped; subscriber will be cleaned up on unsubscribe

    # --- ingest ------------------------------------------------------------------------
    def _vehicle(self, vin):
        v = self.vehicles.get(vin)
        if v is None:
            v = {"fields": {}, "last_epoch": 0.0, "display_name": vin, "client_version": None,
                 "charge_baseline": None, "prime": None, "tesla_id": None, "prime_epoch": 0.0,
                 "history": {"soc": deque(maxlen=HISTORY_MAX), "speed": deque(maxlen=HISTORY_MAX)}}
            self.vehicles[vin] = v
        return v

    def ingest(self, rec):
        """Fold one telemetry record into state and publish a change event. Thread-safe."""
        if rec.get("msg") != "record_payload":
            return
        data = rec.get("data") or {}
        if not isinstance(data, dict):   # a variant/non-record frame; never let it kill the tail
            return
        vin = rec.get("vin") or data.get("Vin")
        if not vin:
            return
        now = time.time()
        created = data.get("CreatedAt", "")
        cver = (rec.get("metadata") or {}).get("device_client_version")
        changed = {}
        with self._lock:
            self.total_records += 1
            self.last_record_epoch = now
            self._record_times.append(now)
            v = self._vehicle(vin)
            v["last_epoch"] = now
            if cver:
                v["client_version"] = cver
            for k, val in data.items():
                if k in fields.META_BASE:
                    continue
                v["fields"][k] = {"value": val, "created_at": created, "received_at": now}
                changed[k] = val
                n = fields.num(val)
                if k == "Soc" and n is not None:
                    v["history"]["soc"].append((now, n))
                elif k == "VehicleSpeed" and n is not None:
                    v["history"]["speed"].append((now, n))
            self._track_charge_baseline(v)
        if changed:
            self._publish({"vin": vin, "changed": changed, "at": now})

    @staticmethod
    def _track_charge_baseline(v):
        """Capture energy-in at charge-session start so charge_energy_added can be derived, and
        reset it when not charging. Mirrors the v0 shim's per-session baseline."""
        f = v["fields"]

        def val(k):
            return f[k]["value"] if k in f else None
        cs = fields.strip_state(val("DetailedChargeState")) or fields.strip_state(val("ChargeState"))
        if cs in ("Charging", "Starting"):
            if v.get("charge_baseline") is None:
                dc = fields.num(val("DCChargingEnergyIn"))
                v["charge_baseline"] = dc if dc is not None else (fields.num(val("ACChargingEnergyIn")) or 0.0)
        else:
            v["charge_baseline"] = None

    # --- Fleet-API prime (the *other* source feeding the same superset) ---------------
    def set_prime(self, vin, prime, tesla_id=None, display_name=None):
        """Fold a Fleet-API vehicle_data snapshot into the per-VIN record. Lives in the Store (not a
        side table) so both the dashboard and the shim read one structure that both sources refresh."""
        now = time.time()
        with self._lock:
            v = self._vehicle(vin)
            v["prime"] = prime
            v["prime_epoch"] = now
            if tesla_id is not None:
                v["tesla_id"] = tesla_id
            if display_name:
                v["display_name"] = display_name

    def get_prime(self, vin):
        with self._lock:
            v = self.vehicles.get(vin)
            return v.get("prime") if v else None

    def display_name(self, vin):
        with self._lock:
            v = self.vehicles.get(vin)
            return (v.get("display_name") if v else None) or vin

    # --- read helpers ------------------------------------------------------------------
    def snapshot(self, vin):
        """A plain dict of {field: value} for the VIN from LIVE telemetry only (no prime). Used by
        the shim/stream, which apply their own prime-merge with ephemeral-safe rules."""
        with self._lock:
            v = self.vehicles.get(vin)
            return {k: f["value"] for k, f in v["fields"].items()} if v else {}

    def merged_snapshot(self, vin):
        """Flat {field: value} of the merged superset (prime base, live overlay; ephemeral gear/speed
        stay live-only). The streaming sink uses this instead of the live-only ``snapshot`` so a
        drive's very first frame already carries the last-known Odometer/RatedRange from the prime.
        Otherwise those slow 60 s fields are blank at second 0, TeslaMate anchors the drive's start
        position to a null odometer, and the whole trip records a null start_km → null distance."""
        return {k: entry["value"] for k, entry in self.merged_fields(vin).items()}

    def merged_fields(self, vin):
        """The telemetry-named *superset* for a VIN: Fleet-API prime as the base layer, overlaid by
        live telemetry (which always wins). Ephemeral gear/speed are never taken from the prime. This
        is what the dashboard renders, so a freshly-restarted (or parked) car still shows a full
        picture from the prime until the live stream fills it in."""
        with self._lock:
            v = self.vehicles.get(vin)
            if not v:
                return {}
            live = {k: dict(f) for k, f in v["fields"].items()}
            prime = v.get("prime")
            prime_epoch = v.get("prime_epoch", 0.0)
        merged = {}
        if prime:
            for k, val in fields.prime_to_fields(prime).items():
                if k in PRIME_EPHEMERAL_FIELDS:
                    continue
                merged[k] = {"value": val, "created_at": "", "received_at": prime_epoch, "source": "prime"}
        for k, entry in live.items():
            entry.setdefault("source", "telemetry")
            merged[k] = entry
        return merged

    def vins(self):
        with self._lock:
            return list(self.vehicles.keys())

    def charge_baseline(self, vin):
        """Energy-in (kWh) captured at the start of the current charge session, or None."""
        with self._lock:
            v = self.vehicles.get(vin)
            return v.get("charge_baseline") if v else None

    def rate_per_min(self):
        now = time.time()
        with self._lock:
            recent = [t for t in self._record_times if now - t <= 600]
        if not recent:
            return 0.0
        span = max(now - recent[0], 1.0)
        return round(len(recent) / span * 60.0, 1)
