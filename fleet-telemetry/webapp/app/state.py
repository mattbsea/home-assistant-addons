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


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


HISTORY_MAX = 600  # samples kept per sparkline series
ONLINE_WINDOW = 660  # seconds of telemetry silence before we fall back to "asleep" (staleness backstop)

# Fields the Fleet-API seed must NEVER write: telemetry expresses them by presence/absence, and a
# (possibly mid-drive) snapshot would strand a live drive state. Telemetry is their sole source.
LIVE_ONLY = ("Gear", "VehicleSpeed")


SD_KEY, TOTAL_KEY = "SelfDrivingMilesSinceReset", "MilesSinceReset"


class Store:
    def __init__(self, sd_persist=None):
        # sd_persist: optional sdtrack.BaselineStore. Without it the since-tracking baseline lives in
        # memory only and is seeded from the first live reading (no record-log lookup).
        self.sd_persist = sd_persist
        self._lock = threading.Lock()
        self.vehicles = {}            # vin -> {fields, history, last_epoch, display_name, ...}
        self.total_records = 0
        self.last_record_epoch = 0.0
        self._record_times = deque(maxlen=5000)   # epochs, for the records/min stat
        self._subscribers = []        # list[(loop, asyncio.Queue)]
        self.charge_starts = queue.Queue()        # vins whose charge session just began -> targeted Fleet fetch
        self.sleep_checks = queue.Queue()         # (vin, disconnect_epoch) on DISCONNECTED -> settle + /products confirm
        self._fleet_calls = {"total": 0, "token": 0, "products": 0, "vehicle_data": 0}  # since add-on start

    # --- Fleet-API call counter (in-memory, since add-on start; no log/file dependency) ----
    def note_fleet_call(self, url):
        """Count one Fleet-API HTTP call, classified by URL. Called at the request choke point."""
        u = url or ""
        if "/products" in u:
            kind = "products"
        elif "/vehicle_data" in u:
            kind = "vehicle_data"
        elif "/oauth2" in u or "/token" in u:
            kind = "token"
        else:
            kind = None
        with self._lock:
            self._fleet_calls["total"] += 1
            if kind:
                self._fleet_calls[kind] += 1

    def fleet_calls(self):
        with self._lock:
            return dict(self._fleet_calls)

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
                 "last_fleet_epoch": 0.0, "sd_increase_epoch": None,
                 "sd_baseline": self.sd_persist.load(vin) if self.sd_persist else None, "sd_scanned": False,
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
        sd_save = sd_scan = None
        with self._lock:
            self.total_records += 1
            self.last_record_epoch = now
            self._record_times.append(now)
            v = self._vehicle(vin)
            v["last_epoch"] = now
            if created:
                v["last_created_at"] = created   # latest telemetry timestamp (for the on-subscribe re-sync frame)
            if cver:
                v["client_version"] = cver
            for k, val in data.items():
                if k in fields.META_BASE:
                    continue
                # An "<invalid>" sentinel means "no reading": it must NEVER clobber a known-good value
                # (seed or prior telemetry) — including Gear. Park is signalled by an explicit
                # ShiftStateP (always sent before any '<invalid>'), so retaining the last real gear is
                # both correct and avoids a redundant clear.
                if val in ("<invalid>", "invalid"):
                    continue
                n = fields.num(val)
                if k == "SelfDrivingMilesSinceReset" and n is not None:
                    # Inferred-FSD signal: note when the counter goes UP (see fields.self_driving_status).
                    # A decrease is a counter reset (never counts) and a resent (buffered) record is old news,
                    # not a live engagement signal — so skip IsResend.
                    prev = fields.num(v["fields"].get(k, {}).get("value"))
                    if prev is not None and n > prev and not data.get("IsResend"):
                        v["sd_increase_epoch"] = now
                v["fields"][k] = {"value": val, "created_at": created, "received_at": now, "source": "telemetry"}
                changed[k] = val
                if k == "Soc" and n is not None:
                    v["history"]["soc"].append((now, n))
                elif k == "VehicleSpeed" and n is not None:
                    v["history"]["speed"].append((now, n))
            # Tesla signals park via a final Gear=ShiftStateP record, never a final VehicleSpeed=0 —
            # the last driving speed (e.g. a few mph right as the car coasted to a stop) would
            # otherwise sit in the store forever. Zero VehicleSpeed here, at the SSOT, the moment park
            # is observed, so every consumer (dashboard, TeslaMate shim, /api/state, future sinks)
            # sees 0 without needing its own Gear-gating logic.
            if fields.strip_state(data.get("Gear")) == "P":
                v["fields"]["VehicleSpeed"] = {"value": 0, "created_at": created, "received_at": now,
                                                "source": "telemetry"}
                changed["VehicleSpeed"] = 0
                v["history"]["speed"].append((now, 0))
            charge_started = self._track_charge_baseline(v)
            # A resent (buffered) record replays old readings: it never seeds, moves or re-anchors the baseline.
            if not data.get("IsResend") and (SD_KEY in changed or TOTAL_KEY in changed):
                sd_save, sd_scan = self._sd_step(vin, v, created or _iso(now))
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
        if sd_scan:
            self._sd_seed_from_log(vin, created or _iso(now))
        elif sd_save:
            self._sd_save(vin, sd_save)
        if charge_started:
            self.charge_starts.put(vin)   # signal the charge-field worker (off the tail thread)
        if disconnected:
            self.sleep_checks.put((vin, now))   # settle window + /products confirm runs off-thread
        if changed:
            # `created_at` is the telemetry CreatedAt (sub-second): the streaming sink stamps positions
            # with it, not wall-clock receive time, so TeslaMate's power/regen-over-dt panels stay accurate.
            self._publish({"vin": vin, "changed": changed, "at": now, "created_at": created})

    # --- self-driving share since tracking began -----------------------------------------
    def _sd_step(self, vin, v, at, seed=None):
        """Advance the vehicle's since-tracking baseline from its current counters. Caller holds the lock.

        Returns (baseline_to_persist | None, needs_log_scan). With no baseline yet and the log not
        consulted, asks the caller to scan the record log first (off the lock); otherwise the baseline is
        `seed` (the log's earliest pair) or the first live reading. If either counter is below its
        baseline the car reset a counter, so the baseline re-anchors at the new reading."""
        f = v["fields"]
        sd = fields.num(f.get(SD_KEY, {}).get("value"))
        total = fields.num(f.get(TOTAL_KEY, {}).get("value"))
        if sd is None or total is None:
            return None, False
        b, changed = v["sd_baseline"], False
        if b is None:
            if seed is None and not v["sd_scanned"]:
                return None, True
            b = seed or {"sd_miles": sd, "total_miles": total, "at": at, "source": "live", "reanchor": None}
            v["sd_baseline"], changed = b, True
            print(f"[app] self-driving baseline for ...{vin[-4:]} from "
                  f"{'the record log' if b['source'] == 'record_log' else 'the first live reading'}: "
                  f"{b['sd_miles']} self-driving / {b['total_miles']} total miles at {b['at']}", flush=True)
        if sd < b["sd_miles"] or total < b["total_miles"]:
            why = ("self-driving miles" if sd < b["sd_miles"] else "total miles") + " went down (counter reset)"
            v["sd_baseline"] = b = {"sd_miles": sd, "total_miles": total, "at": at, "source": "reanchor",
                                    "reanchor": {"at": at, "reason": why, "previous_sd_miles": b["sd_miles"],
                                                 "previous_total_miles": b["total_miles"]}}
            changed = True
            print(f"[app] self-driving baseline for ...{vin[-4:]} re-anchored at {sd} / {total} at {at}: {why}",
                  flush=True)
        return (b if changed else None), False

    def _sd_seed_from_log(self, vin, at):
        """First counter reading for a vehicle with no stored baseline: look in the persistent record log
        (streamed, off the store lock, once per vehicle per run), then settle the baseline."""
        found = self.sd_persist.scan_log(vin) if self.sd_persist else None
        with self._lock:
            v = self.vehicles[vin]
            v["sd_scanned"] = True
            persist, _ = self._sd_step(vin, v, at, seed=found)
        if persist:
            self._sd_save(vin, persist)

    def _sd_save(self, vin, baseline):
        if self.sd_persist:
            try:
                self.sd_persist.save(vin, baseline)
            except Exception as exc:   # persistence is best-effort; never take down ingest
                print(f"[app] could not persist self-driving baseline: {exc!r}", flush=True)

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
        """Prime the field map from a Fleet-API vehicle_data response (mapped to telemetry names). Used
        for the one-time startup seed AND the bridge poll while the stream is down — `last_fleet_epoch`
        keeps the vehicle 'online' between bridge polls, and a successful seed clears any sleep_state."""
        now = time.time()
        with self._lock:
            v = self._vehicle(vin)
            self._write_fleet(v, fields.fleet_api_to_fields(vehicle_data))
            v["seed_epoch"] = now
            v["last_fleet_epoch"] = now
            v["sleep_state"] = None
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

    def last_created_at(self, vin):
        """The most recent telemetry CreatedAt for the VIN, or None — used to stamp the on-subscribe
        re-sync frame with telemetry time instead of wall-clock."""
        with self._lock:
            v = self.vehicles.get(vin)
            return v.get("last_created_at") if v else None

    # --- sleep detection ---------------------------------------------------------------
    def set_sleep_state(self, vin, state):
        """Record the Fleet-API-confirmed non-online state ('asleep'/'offline') for the VIN, stamping
        when it was confirmed so the monitor can re-confirm it later (see sleep_recheck_due)."""
        with self._lock:
            v = self.vehicles.get(vin)
            if v:
                v["sleep_state"] = state
                v["sleep_state_epoch"] = time.time()

    def sleep_recheck_due(self, vin, recheck_secs):
        """True if a confirmed asleep/offline state is older than `recheck_secs` and should be
        re-confirmed via /products. A sleeping car never streams to clear the state, and Tesla can move
        it offline<->asleep<->online while silent — so the monitor must re-poll (a no-wake /products
        call) instead of latching the first reading forever."""
        with self._lock:
            v = self.vehicles.get(vin)
            if not v or not v.get("sleep_state"):
                return False
            return (time.time() - v.get("sleep_state_epoch", 0.0)) >= recheck_secs

    def sleep_state(self, vin):
        """The confirmed non-online state, or None. The monitor uses it to stop polling a sleeping car."""
        with self._lock:
            v = self.vehicles.get(vin)
            return v.get("sleep_state") if v else None

    def reconnected_since(self, vin, since):
        """True if the car reconnected or streamed real telemetry after `since` — cancels a sleep check."""
        with self._lock:
            v = self.vehicles.get(vin)
            if not v:
                return False
            return max(v.get("last_data_epoch", 0.0), v.get("last_connect_epoch", 0.0)) > since

    def streaming(self, vin, threshold):
        """True if the telemetry stream is delivering — real telemetry or a connect within `threshold`s.
        Deliberately excludes Fleet seeds (last_fleet_epoch) so the bridge keeps polling while the stream
        is down even though its own seeds refresh the data."""
        now = time.time()
        with self._lock:
            v = self.vehicles.get(vin)
            if not v:
                return False
            return (now - max(v.get("last_data_epoch", 0.0), v.get("last_connect_epoch", 0.0))) < threshold

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
            # A recent bridge seed (last_fleet_epoch) keeps the car "online" between bridge polls, so a
            # stream-down-but-charging car isn't tripped into the staleness backstop.
            last = max(v.get("last_data_epoch", 0.0), v.get("last_fleet_epoch", 0.0))
            fresh = last and (now - last) < ONLINE_WINDOW
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

    def self_driving(self, vin, now=None, window=fields.SELF_DRIVING_WINDOW_SECONDS):
        """Inferred self-driving status for the VIN (fields.self_driving_status) — computed here so the
        dashboard and any other reader share one definition and one set of timestamps."""
        now = time.time() if now is None else now
        with self._lock:
            v = self.vehicles.get(vin)
            if not v:
                return fields.self_driving_status({}, None, now, window)
            snap = {k: f["value"] for k, f in v["fields"].items()}
            return fields.self_driving_status(snap, v.get("sd_increase_epoch"), now, window,
                                              baseline=v.get("sd_baseline"))

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
