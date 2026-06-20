"""Per-VIN telemetry state + async pub/sub event bus — the heart of the v1 app.

ONE per-VIN field map is the single source of truth. Two writers feed it:
  - the live telemetry stream (continuous) — the sole writer for every streamable field, and
  - a one-time Fleet-API **seed** at startup, plus a targeted refresh of the two non-streamed charge
    fields (charger_pilot_current / fast_charger_brand) when a charge session begins.
Merge is **last-writer-wins**: telemetry writes continuously, so it owns every field it carries; the
seed fills the rest. There is no read-time merge and no separate "prime" blob — sinks (dashboard SSE,
TeslaMate shim, streaming ws) all read this one structure via `snapshot`/`fields_view`.
"""
import asyncio
import queue
import threading
import time
from collections import deque

import fields

HISTORY_MAX = 600  # samples kept per sparkline series
ONLINE_WINDOW = 660  # seconds of telemetry silence before we fall back to "asleep" (staleness backstop)

# Fields the Fleet-API seed must NEVER write: telemetry expresses them by presence/absence, and a
# (possibly mid-drive) snapshot would strand a live drive state. Telemetry is their sole source.
LIVE_ONLY = ("Gear", "VehicleSpeed")


class Store:
    def __init__(self):
        self._lock = threading.Lock()
        self.vehicles = {}            # vin -> {fields, history, last_epoch, display_name, ...}
        self.total_records = 0
        self.last_record_epoch = 0.0
        self._record_times = deque(maxlen=5000)   # epochs, for the records/min stat
        self._subscribers = []        # list[(loop, asyncio.Queue)]
        self.charge_starts = queue.Queue()        # vins whose charge session just began -> targeted Fleet fetch
        self.sleep_checks = queue.Queue()         # (vin, disconnect_epoch) on DISCONNECTED -> settle + /products confirm

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
                 "charge_baseline": None, "tesla_id": None, "seed_epoch": 0.0,
                 "connected": True, "sleep_state": None, "last_data_epoch": 0.0, "last_connect_epoch": 0.0,
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
        meta = rec.get("metadata") or {}
        cver = meta.get("device_client_version")
        # Connectivity frames carry the socket up/down signal (txtype=="connectivity", data.Status).
        status = data.get("Status")
        is_connectivity = meta.get("txtype") == "connectivity" or status is not None
        changed = {}
        charge_started = False
        disconnected = False
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
                # An "<invalid>" sentinel means "no reading": it must not clobber a known-good value
                # (seed or prior telemetry) — EXCEPT for LIVE_ONLY fields, where "<invalid>" is the
                # meaningful signal that the live state ended (Gear -> parked) and must clear it.
                if val in ("<invalid>", "invalid") and k not in LIVE_ONLY:
                    continue
                v["fields"][k] = {"value": val, "created_at": created, "received_at": now, "source": "telemetry"}
                changed[k] = val
                n = fields.num(val)
                if k == "Soc" and n is not None:
                    v["history"]["soc"].append((now, n))
                elif k == "VehicleSpeed" and n is not None:
                    v["history"]["speed"].append((now, n))
            charge_started = self._track_charge_baseline(v)
            # Connectivity vs liveness. A real telemetry record means the car is streaming = awake.
            # A CONNECTED frame is also a wake signal; a DISCONNECTED frame starts a sleep check.
            if is_connectivity:
                if status == "CONNECTED":
                    v["connected"], v["sleep_state"], v["last_connect_epoch"] = True, None, now
                elif status == "DISCONNECTED":
                    v["connected"] = False
                    disconnected = True
            else:
                v["connected"], v["sleep_state"], v["last_data_epoch"] = True, None, now
        if charge_started:
            self.charge_starts.put(vin)   # signal the charge-field worker (off the tail thread)
        if disconnected:
            self.sleep_checks.put((vin, now))   # settle window + /products confirm runs off-thread
        if changed:
            self._publish({"vin": vin, "changed": changed, "at": now})

    @staticmethod
    def _track_charge_baseline(v):
        """Capture energy-in at charge-session start so charge_energy_added can be derived, and reset
        it when not charging. Returns True on the not-charging -> Charging/Starting edge (so the caller
        can trigger the one-shot Fleet fetch for the non-streamed charge fields)."""
        f = v["fields"]

        def val(k):
            return f[k]["value"] if k in f else None
        cs = fields.strip_state(val("DetailedChargeState")) or fields.strip_state(val("ChargeState"))
        if cs in ("Charging", "Starting"):
            if v.get("charge_baseline") is None:
                dc = fields.num(val("DCChargingEnergyIn"))
                v["charge_baseline"] = dc if dc is not None else (fields.num(val("ACChargingEnergyIn")) or 0.0)
                return True   # charge-session start edge
        else:
            v["charge_baseline"] = None
        return False

    # --- Fleet-API writer (seed once at startup + targeted charge-field refresh) -------
    def _write_fleet(self, v, mapping):
        """Write a telemetry-named mapping into the field map with source='fleet' (last-writer-wins;
        telemetry overwrites later). LIVE_ONLY and None values are never written."""
        now = time.time()
        for k, val in mapping.items():
            if k in LIVE_ONLY or val is None:
                continue
            v["fields"][k] = {"value": val, "created_at": "", "received_at": now, "source": "fleet"}

    def seed(self, vin, vehicle_data, tesla_id=None, display_name=None):
        """Prime the field map once from a Fleet-API vehicle_data response (mapped to telemetry names)."""
        with self._lock:
            v = self._vehicle(vin)
            self._write_fleet(v, fields.fleet_api_to_fields(vehicle_data))
            v["seed_epoch"] = time.time()
            if tesla_id is not None:
                v["tesla_id"] = tesla_id
            if display_name:
                v["display_name"] = display_name

    def update_charge_fields(self, vin, mapping):
        """Targeted write of the two non-streamed charge fields (telemetry-named:
        ChargerPilotCurrent / FastChargerBrand) at charge start."""
        with self._lock:
            self._write_fleet(self._vehicle(vin), mapping)

    def tesla_id(self, vin):
        with self._lock:
            v = self.vehicles.get(vin)
            return v.get("tesla_id") if v else None

    def display_name(self, vin):
        with self._lock:
            v = self.vehicles.get(vin)
            return (v.get("display_name") if v else None) or vin

    # --- sleep detection ---------------------------------------------------------------
    def set_sleep_state(self, vin, state):
        """Record the Fleet-API-confirmed non-online state ('asleep'/'offline') for the VIN."""
        with self._lock:
            v = self.vehicles.get(vin)
            if v:
                v["sleep_state"] = state

    def reconnected_since(self, vin, since):
        """True if the car reconnected or streamed real telemetry after `since` — cancels a sleep check."""
        with self._lock:
            v = self.vehicles.get(vin)
            if not v:
                return False
            return max(v.get("last_data_epoch", 0.0), v.get("last_connect_epoch", 0.0)) > since

    def vehicle_state(self, vin):
        """Authoritative state for the shim (TeslaMate) and dashboard: 'online' / 'asleep' / 'offline'.
        A /products-confirmed sleep_state wins; otherwise online when ready + recent telemetry, else the
        staleness backstop ('asleep') so we never hang 'online' if the /products confirm never lands."""
        now = time.time()
        with self._lock:
            v = self.vehicles.get(vin)
            if not v:
                return "offline"
            if v.get("sleep_state"):
                return v["sleep_state"]
            f = v["fields"]

            def val(k):
                return f[k]["value"] if k in f else None
            has_batt = fields.num(val("Soc")) is not None or fields.num(val("BatteryLevel")) is not None
            has_loc = fields.parse_location(val("Location"))[0] is not None
            fresh = v.get("last_data_epoch", 0.0) and (now - v["last_data_epoch"]) < ONLINE_WINDOW
        return "online" if (fresh and has_batt and has_loc) else "asleep"

    # --- reads -------------------------------------------------------------------------
    def snapshot(self, vin):
        """Flat {field: value} for the VIN — the unified superset (telemetry overlaid on the seed)."""
        with self._lock:
            v = self.vehicles.get(vin)
            return {k: f["value"] for k, f in v["fields"].items()} if v else {}

    def fields_view(self, vin):
        """{field: {value, source, received_at, created_at}} — the unified entries (for the dashboard)."""
        with self._lock:
            v = self.vehicles.get(vin)
            return {k: dict(f) for k, f in v["fields"].items()} if v else {}

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
