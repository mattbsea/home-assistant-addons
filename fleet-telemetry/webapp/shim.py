#!/usr/bin/env python3
"""Fleet-API shim for TeslaMate.

Serves a tiny subset of Tesla's Fleet API — the three read-only endpoints TeslaMate v4 calls —
assembled entirely from the local fleet-telemetry stream. Point TeslaMate's TESLA_API_HOST at this
server (and set each car's `use_streaming_api = false`) and TeslaMate gets its vehicle data from us
instead of polling Tesla, at zero Fleet-API cost.

Design notes (verified against teslamate-org/teslamate v4.0.1):
- Endpoints: GET /api/1/products, GET /api/1/vehicles/{id}, GET /api/1/vehicles/{id}/vehicle_data.
  (TeslaMate never sends wake/commands on the Fleet path.) Each returns 200 + {"response": ...}.
- vehicle_data MUST contain all five sections (drive_state, charge_state, climate_state,
  vehicle_state, vehicle_config) with a non-null vehicle_config and a valid, monotonically
  non-decreasing millisecond `timestamp`, or TeslaMate crashes/discards the poll.
- Values are in Tesla-native units (mph, miles, °C, bar) — TeslaMate converts internally.
- Cold start: until the essential fields are known we report state "asleep" (fuse-safe; TeslaMate
  keeps polling the cheap endpoint and never asks for vehicle_data) and answer vehicle_data with
  HTTP 408 "vehicle unavailable" (also fuse-safe) as a belt-and-suspenders. We NEVER return 5xx.
- State survives add-on restarts via a JSON checkpoint in /data, so a restart warm-starts.

Items still pending validation against a real drive + charge are marked TODO(validate).
"""

import json
import os
import re
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RECORDS_FILE = os.environ.get("FT_RECORDS_FILE", "/tmp/ft-records.jsonl")
PORT = int(os.environ.get("FT_SHIM_PORT", "8085"))
STATE_FILE = os.environ.get("FT_SHIM_STATE", "/data/shim-state.json")
# A record must have arrived within this window for the car to be considered awake/online.
ONLINE_WINDOW = int(os.environ.get("FT_SHIM_ONLINE_WINDOW", "660"))
SAVE_EVERY = 15  # seconds between state checkpoints

# Vehicle identity (stable; TeslaMate dedups by VIN, so these need only be consistent).
VIN = os.environ.get("FT_SHIM_VIN", "REDACTED_VIN")
VEHICLE_ID = int(os.environ.get("FT_SHIM_VEHICLE_ID", "2252258131777778"))
EID = int(os.environ.get("FT_SHIM_EID", "REDACTED_EXT_ID_2"))
DISPLAY_NAME = os.environ.get("FT_SHIM_NAME", "REDACTED_DISPLAY_NAME")
# Static config object — must be non-null & parseable. Empty {} is safe (TeslaMate keeps its
# existing identification). TODO(validate): bake real car_type/trim/color/wheels for nicer display.
VEHICLE_CONFIG = {}

_META = {"CreatedAt", "IsResend", "Vin", "ConnectionID", "NetworkInterface", "Status"}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_loc(val):
    if isinstance(val, dict):
        lat = val.get("latitude", val.get("Latitude"))
        lon = val.get("longitude", val.get("Longitude"))
        return _num(lat), _num(lon)
    return None, None


def _strip_state(v):
    """'DetailedChargeStateDisconnected' -> 'Disconnected'; '<invalid>' -> None."""
    if v is None or not isinstance(v, str):
        return v
    if v in ("<invalid>", "invalid", ""):
        return None
    i = v.rfind("State")
    if 0 <= i and i + 5 < len(v):
        return v[i + 5:]
    return v


class VehicleStore:
    """Latest-value-per-field for one VIN, fed by tailing the telemetry logger output."""

    def __init__(self):
        self.lock = threading.Lock()
        self.fields = {}          # field -> latest value
        self.last_epoch = 0.0     # wall-clock of most recent record
        self.charge_baseline = None  # kWh (AC+DC) at the start of the current charge session
        self.last_ts = 0          # last emitted ms timestamp (monotonic guard)
        self._load()

    # --- persistence -------------------------------------------------------
    def _load(self):
        try:
            with open(STATE_FILE) as fh:
                d = json.load(fh)
            self.fields = d.get("fields", {})
            self.last_epoch = d.get("last_epoch", 0.0)
            self.charge_baseline = d.get("charge_baseline")
            self.last_ts = d.get("last_ts", 0)
            print(f"[shim] warm-started from {STATE_FILE}: {len(self.fields)} fields", flush=True)
        except (OSError, ValueError):
            print("[shim] no prior state; cold start", flush=True)

    def save(self):
        tmp = STATE_FILE + ".tmp"
        try:
            with self.lock:
                d = {"fields": self.fields, "last_epoch": self.last_epoch,
                     "charge_baseline": self.charge_baseline, "last_ts": self.last_ts}
            with open(tmp, "w") as fh:
                json.dump(d, fh)
            os.replace(tmp, STATE_FILE)
        except OSError:
            pass

    # --- ingest ------------------------------------------------------------
    def ingest(self, obj):
        if obj.get("msg") != "record_payload":
            return
        data = obj.get("data") or {}
        with self.lock:
            self.last_epoch = time.time()
            for k, v in data.items():
                if k in _META:
                    continue
                self.fields[k] = v
            self._update_charge_session()

    def _update_charge_session(self):  # call holding lock
        active = self._charging_active_locked()
        if active:
            if self.charge_baseline is None:
                self.charge_baseline = self._energy_in_locked()
        else:
            self.charge_baseline = None

    def _charging_active_locked(self):
        cs = _strip_state(self.fields.get("DetailedChargeState")) or _strip_state(self.fields.get("ChargeState"))
        return cs in ("Charging", "Starting")

    def _energy_in_locked(self):
        ac = _num(self.fields.get("ACChargingEnergyIn")) or 0.0
        dc = _num(self.fields.get("DCChargingEnergyIn")) or 0.0
        return ac + dc

    # --- derived state -----------------------------------------------------
    def _ready_locked(self):
        f = self.fields
        has_batt = _num(f.get("Soc")) is not None or _num(f.get("BatteryLevel")) is not None
        has_odo = _num(f.get("Odometer")) is not None
        has_loc = _parse_loc(f.get("Location"))[0] is not None
        return has_batt and has_odo and has_loc

    def state_str(self):
        with self.lock:
            fresh = self.last_epoch and (time.time() - self.last_epoch) < ONLINE_WINDOW
            return "online" if (fresh and self._ready_locked()) else "asleep"

    def ready(self):
        with self.lock:
            return self._ready_locked()

    def _ts(self):  # monotonic ms timestamp, persisted via last_ts
        ts = max(int(time.time() * 1000), self.last_ts + 1)
        self.last_ts = ts
        return ts

    # --- shape into Tesla vehicle_data ------------------------------------
    def vehicle_data(self):
        with self.lock:
            f = self.fields
            ts = self._ts()

            # drive_state -------------------------------------------------
            lat, lon = _parse_loc(f.get("Location"))
            gear = _strip_state(f.get("Gear"))
            shift = gear if gear in ("D", "R", "N") else None  # P/Invalid -> null (parked)
            driving = shift in ("D", "R", "N")
            # TODO(validate): drive power = PackVoltage * PackCurrent / 1000; sign/scale unconfirmed.
            pv, pc = _num(f.get("PackVoltage")), _num(f.get("PackCurrent"))
            power = round(pv * pc / 1000.0, 1) if (driving and pv is not None and pc is not None) else 0
            drive_state = {
                "timestamp": ts,
                "latitude": lat, "longitude": lon,
                "heading": _num(f.get("GpsHeading")),
                "speed": _num(f.get("VehicleSpeed")) if driving else None,
                "power": power,
                "shift_state": shift,
            }

            # charge_state ------------------------------------------------
            charging_state = _strip_state(f.get("DetailedChargeState")) or _strip_state(f.get("ChargeState")) or "Disconnected"
            ac_p, dc_p = _num(f.get("ACChargingPower")) or 0.0, _num(f.get("DCChargingPower")) or 0.0
            # TODO(validate): charge_energy_added as session delta off cumulative *ChargingEnergyIn.
            energy_added = 0
            if self.charge_baseline is not None:
                energy_added = round(self._energy_in_locked() - self.charge_baseline, 3)
            charge_state = {
                "timestamp": ts,
                "charging_state": charging_state,
                "battery_level": _round_int(f.get("Soc") if _num(f.get("Soc")) is not None else f.get("BatteryLevel")),
                "usable_battery_level": _round_int(f.get("BatteryLevel") if _num(f.get("BatteryLevel")) is not None else f.get("Soc")),
                "battery_range": _num(f.get("RatedRange")),
                "est_battery_range": _num(f.get("EstBatteryRange")),
                "ideal_battery_range": _num(f.get("IdealBatteryRange")),
                "charge_energy_added": energy_added,
                "charger_actual_current": _round_int(f.get("ChargeAmps")),
                "charger_phases": _round_int(f.get("ChargerPhases")),
                "charger_pilot_current": None,
                "charger_power": round(ac_p + dc_p, 1),
                "charger_voltage": _round_int(f.get("ChargerVoltage")),
                "conn_charge_cable": _strip_state(f.get("ChargingCableType")) or "<invalid>",
                "fast_charger_present": _bool(f.get("FastChargerPresent")),
                "fast_charger_brand": "<invalid>",
                "fast_charger_type": _strip_state(f.get("FastChargerType")) or "<invalid>",
                "not_enough_power_to_heat": None,
                "battery_heater_on": False,
                "scheduled_charging_start_time": None,
                "time_to_full_charge": _num(f.get("TimeToFullCharge")) or 0,
            }

            # climate_state ----------------------------------------------
            climate_state = {
                "timestamp": ts,
                "outside_temp": _num(f.get("OutsideTemp")),
                "inside_temp": _num(f.get("InsideTemp")),
                "is_climate_on": _bool(f.get("HvacACEnabled")) or _strip_state(f.get("HvacPower")) == "On",
                "is_preconditioning": False,
                "climate_keeper_mode": (_strip_state(f.get("ClimateKeeperMode")) or "off").lower(),
                "fan_status": _round_int(f.get("HvacFanStatus")) or 0,
                "driver_temp_setting": _num(f.get("HvacLeftTemperatureRequest")),
                "passenger_temp_setting": _num(f.get("HvacRightTemperatureRequest")),
                "is_front_defroster_on": False,
                "is_rear_defroster_on": False,
                "battery_heater": False,
                "battery_heater_no_power": None,
            }

            # vehicle_state ----------------------------------------------
            doors = f.get("DoorState") if isinstance(f.get("DoorState"), dict) else {}
            sentry = _strip_state(f.get("SentryMode")) in ("Armed", "On", "Enabled")
            vehicle_state = {
                "timestamp": ts,
                "odometer": _num(f.get("Odometer")),
                "car_version": f.get("Version") if isinstance(f.get("Version"), str) else "",
                "locked": _bool(f.get("Locked")),
                "sentry_mode": sentry,
                "is_user_present": False,
                "df": 1 if doors.get("DriverFront") else 0,
                "pf": 1 if doors.get("PassengerFront") else 0,
                "dr": 1 if doors.get("DriverRear") else 0,
                "pr": 1 if doors.get("PassengerRear") else 0,
                "ft": 1 if doors.get("TrunkFront") else 0,
                "rt": 1 if doors.get("TrunkRear") else 0,
                "tpms_pressure_fl": _num(f.get("TpmsPressureFl")),
                "tpms_pressure_fr": _num(f.get("TpmsPressureFr")),
                "tpms_pressure_rl": _num(f.get("TpmsPressureRl")),
                "tpms_pressure_rr": _num(f.get("TpmsPressureRr")),
                "software_update": {"status": "", "download_perc": 0, "install_perc": 0, "version": ""},
            }

            return {
                "id": EID, "vehicle_id": VEHICLE_ID, "vin": VIN,
                "state": "online", "display_name": DISPLAY_NAME, "in_service": False,
                "drive_state": drive_state, "charge_state": charge_state,
                "climate_state": climate_state, "vehicle_state": vehicle_state,
                "vehicle_config": VEHICLE_CONFIG,
            }

    def identity(self, state):
        return {"id": EID, "vehicle_id": VEHICLE_ID, "vin": VIN,
                "state": state, "display_name": DISPLAY_NAME, "in_service": False}


def _round_int(v):
    n = _num(v)
    return int(round(n)) if n is not None else None


def _bool(v):
    return v if isinstance(v, bool) else False


STORE = VehicleStore()


def _tail():
    pos = 0
    while True:
        try:
            if not os.path.exists(RECORDS_FILE):
                time.sleep(1.0)
                continue
            with open(RECORDS_FILE, "r", errors="replace") as fh:
                fh.seek(0, os.SEEK_END)
                if fh.tell() < pos:
                    pos = 0
                fh.seek(pos)
                while True:
                    line = fh.readline()
                    if not line:
                        pos = fh.tell()
                        try:
                            if os.path.getsize(RECORDS_FILE) < pos:
                                break
                        except OSError:
                            break
                        time.sleep(0.5)
                        continue
                    line = line.strip()
                    if line and line[0] == "{":
                        try:
                            STORE.ingest(json.loads(line))
                        except (ValueError, KeyError):
                            pass
        except OSError:
            time.sleep(1.0)


def _saver():
    while True:
        time.sleep(SAVE_EVERY)
        STORE.save()


_PATH_DATA = re.compile(r"^/api/1/vehicles/(\d+)/vehicle_data$")
_PATH_ONE = re.compile(r"^/api/1/vehicles/(\d+)$")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        state = STORE.state_str()

        if path == "/api/1/products":
            self._json(200, {"response": [STORE.identity(state)], "count": 1})
            return
        if path in ("/api/1/vehicles", "/api/1/vehicles/"):
            self._json(200, {"response": [STORE.identity(state)], "count": 1})
            return
        if _PATH_ONE.match(path):
            self._json(200, {"response": STORE.identity(state)})
            return
        if _PATH_DATA.match(path):
            if not STORE.ready():
                # Fuse-safe "temporarily unavailable, retry": TeslaMate treats 408 as
                # :vehicle_unavailable and falls back without melting any breaker.
                self._json(408, {"error": "vehicle unavailable: data not ready", "error_description": ""})
                return
            self._json(200, {"response": STORE.vehicle_data()})
            return
        # Unknown path: 404 not_found shape (harmless; not on TeslaMate's poll path).
        self._json(404, {"error": "not_found"})

    do_HEAD = do_GET


def main():
    threading.Thread(target=_tail, daemon=True).start()
    threading.Thread(target=_saver, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[shim] Fleet-API shim listening on :{PORT} (vin {VIN})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
