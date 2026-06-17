#!/usr/bin/env python3
"""Fleet-API shim for TeslaMate.

Serves the three read-only Fleet API endpoints TeslaMate v4 polls, assembled from the local
fleet-telemetry stream, so TeslaMate (TESLA_API_HOST -> here, use_streaming_api=false) gets its data
from us instead of polling Tesla — at ~zero Fleet-API cost (streaming is free).

Identity is fully dynamic: vehicles are discovered from the telemetry stream (records carry the VIN)
and/or from the prime call. TeslaMate only needs a stable id to echo back and dedups by VIN, so we
synthesize a deterministic id/vehicle_id from each VIN — no real Tesla IDs are hardcoded or required.

Priming (optional): if a Tesla client_id + refresh token are configured (per-user add-on options), on
startup the shim makes one real Fleet-API call to discover the vehicle list and prime a COMPLETE
snapshot per online car — including fields telemetry never carries (vehicle_config, gui_settings,
display name). Live telemetry then overlays the dynamic fields. Wake-guarded (only fetches
vehicle_data for cars already online) and ~$0.002 per restart. Rotated refresh tokens are persisted.

Verified vs teslamate-org/teslamate v4.0.1: all five vehicle_data sections must be present, with a
non-null vehicle_config and a monotonically non-decreasing ms timestamp, or TeslaMate crashes. Units
are Tesla-native (mph/miles/°C/bar). Cold start reports "asleep" + answers vehicle_data 408 (both
fuse-safe) until ready. State persists to /data for warm restarts.
"""

import hashlib
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RECORDS_FILE = os.environ.get("FT_RECORDS_FILE", "/tmp/ft-records.jsonl")
PORT = int(os.environ.get("FT_SHIM_PORT", "8085"))
STATE_FILE = os.environ.get("FT_SHIM_STATE", "/data/shim-state.json")
ONLINE_WINDOW = int(os.environ.get("FT_SHIM_ONLINE_WINDOW", "660"))
SAVE_EVERY = 15

# Priming credentials / hosts (user-supplied add-on options; nothing personal is hardcoded).
CLIENT_ID = os.environ.get("FT_SHIM_CLIENT_ID", "")
REFRESH_SEED = os.environ.get("FT_SHIM_REFRESH_TOKEN", "")
AUTH_HOST = os.environ.get("FT_SHIM_AUTH_HOST", "https://auth.tesla.com")
FLEET_HOST = os.environ.get("FT_SHIM_FLEET_HOST", "https://fleet-api.prd.na.vn.cloud.tesla.com")
# Add-on restarts are rare, so by default we wake a sleeping car once on startup to grab a complete
# fresh snapshot. Set false to be battery-conservative (then priming skips cars that are asleep).
WAKE_ON_PRIME = os.environ.get("FT_SHIM_WAKE_ON_PRIME", "true").lower() == "true"

_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
_META = {"CreatedAt", "IsResend", "Vin", "ConnectionID", "NetworkInterface", "Status"}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _round_int(v):
    n = _num(v)
    return int(round(n)) if n is not None else None


def _fan_speed(v):
    """Parse HvacFanStatus enum ('HvacFanStatusSpeed3') or bare int to an integer fan level."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v)
    if "Off" in s:
        return 0
    m = re.search(r"\d+", s)
    return int(m.group()) if m else None


def _window_state(v):
    """Parse window state enum ('WindowStateClosed', 'WindowStateVenting', 'WindowStateOpen')
    to TeslaMate integer: 0=closed, 1=venting, 2=open."""
    if v is None:
        return None
    s = str(v)
    if "Closed" in s:
        return 0
    if "Vent" in s:
        return 1
    if "Open" in s or "Partial" in s:
        return 2
    return None


def _defrost_on(v):
    """Parse DefrostMode enum — any non-Off value means defrost is active."""
    if v is None:
        return None
    return "Off" not in str(v)


def _parse_loc(val):
    if isinstance(val, dict):
        return _num(val.get("latitude", val.get("Latitude"))), _num(val.get("longitude", val.get("Longitude")))
    return None, None


def _strip_state(v):
    if v is None or not isinstance(v, str):
        return v
    if v in ("<invalid>", "invalid", ""):
        return None
    i = v.rfind("State")
    if 0 <= i and i + 5 < len(v):
        return v[i + 5:]
    return v


def _synth_id(vin, salt):
    """Deterministic, stable id from a VIN (TeslaMate just echoes it back). Kept to 52 bits so it
    stays within IEEE-754 safe-integer range (< 2^53) — like real Tesla IDs — so any JSON consumer
    that parses numbers as doubles can't silently round it."""
    return int(hashlib.sha1((salt + vin).encode()).hexdigest()[:13], 16)


def _http_post_form(url, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _http_get(url, token):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def _http_post_bearer(url, token):
    req = urllib.request.Request(url, data=b"", method="POST", headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


class Vehicle:
    def __init__(self, vin, d=None):
        self.vin = vin
        self.ext_id = _synth_id(vin, "id:")            # id exposed to TeslaMate (path id)
        self.ext_vehicle_id = _synth_id(vin, "vid:")
        d = d or {}
        self.fields = d.get("fields", {})
        self.last_epoch = d.get("last_epoch", 0.0)
        self.charge_baseline = d.get("charge_baseline")
        self.last_ts = d.get("last_ts", 0)
        self.prime = d.get("prime")
        self.tesla_id = d.get("tesla_id")              # real Tesla id, learned from prime (for API calls)
        self.display_name = d.get("display_name") or vin
        if d:  # warm-start: drop ephemeral fields — they go stale and mislead TeslaMate
            for _f in ("Gear", "VehicleSpeed", "PackCurrent", "PackVoltage",
                       "DetailedChargeState", "ChargeState"):
                self.fields.pop(_f, None)

    def dump(self):
        return {"fields": self.fields, "last_epoch": self.last_epoch, "charge_baseline": self.charge_baseline,
                "last_ts": self.last_ts, "prime": self.prime, "tesla_id": self.tesla_id,
                "display_name": self.display_name}

    # --- ingest / charge session ------------------------------------------
    def ingest(self, data):
        self.last_epoch = time.time()
        for k, v in data.items():
            if k not in _META:
                self.fields[k] = v
        if self._charging_active():
            if self.charge_baseline is None:
                self.charge_baseline = self._energy_in()
        else:
            self.charge_baseline = None

    def _charging_active(self):
        cs = _strip_state(self.fields.get("DetailedChargeState")) or _strip_state(self.fields.get("ChargeState"))
        return cs in ("Charging", "Starting")

    def _energy_in(self):
        # ACChargingEnergyIn = AC wall draw; DCChargingEnergyIn = energy stored in battery.
        # They measure the same energy at different sides of the onboard converter — summing
        # them double-counts. Tesla's Fleet API charge_energy_added is the battery-stored (DC)
        # side. Fall back to AC only if DC hasn't arrived yet (e.g., very first record).
        dc = _num(self.fields.get("DCChargingEnergyIn"))
        if dc is not None:
            return dc
        return _num(self.fields.get("ACChargingEnergyIn")) or 0.0

    # --- readiness / state -------------------------------------------------
    def ready(self):
        if self.prime:
            return True
        f = self.fields
        has_batt = _num(f.get("Soc")) is not None or _num(f.get("BatteryLevel")) is not None
        return has_batt and _parse_loc(f.get("Location"))[0] is not None

    def state_str(self):
        fresh = self.last_epoch and (time.time() - self.last_epoch) < ONLINE_WINDOW
        return "online" if (fresh and self.ready()) else "asleep"

    def _ts(self):
        ts = max(int(time.time() * 1000), self.last_ts + 1)
        self.last_ts = ts
        return ts

    def identity(self):
        return {"id": self.ext_id, "vehicle_id": self.ext_vehicle_id, "vin": self.vin,
                "state": self.state_str(), "display_name": self.display_name, "in_service": False}

    # --- assemble ----------------------------------------------------------
    def _assemble(self):
        f = self.fields
        ts = self._ts()
        lat, lon = _parse_loc(f.get("Location"))
        gear = _strip_state(f.get("Gear"))
        shift = gear if gear in ("D", "R", "N") else None
        driving = shift in ("D", "R", "N")
        pv, pc = _num(f.get("PackVoltage")), _num(f.get("PackCurrent"))
        # PackCurrent sign: negative = discharging (driving), positive = charging (regen).
        # Fleet API drive_state.power convention: positive = consuming, negative = regenerating.
        # So negate the product to match what TeslaMate expects.
        power = round(-pv * pc / 1000.0, 1) if (driving and pv is not None and pc is not None) else (None if driving else 0)
        drive_state = {"timestamp": ts, "latitude": lat, "longitude": lon, "heading": _num(f.get("GpsHeading")),
                       "speed": _num(f.get("VehicleSpeed")) if driving else None, "power": power, "shift_state": shift,
                       "active_route_destination": f.get("DestinationName") or f.get("Destination") or None,
                       "active_route_miles_to_arrival": _num(f.get("MilesToArrival")),
                       "active_route_minutes_to_arrival": _num(f.get("MinutesToArrival"))}

        ac_p, dc_p = _num(f.get("ACChargingPower")), _num(f.get("DCChargingPower"))
        charger_power = int(round((ac_p or 0) + (dc_p or 0))) if (ac_p is not None or dc_p is not None) else None
        energy_added = round(self._energy_in() - self.charge_baseline, 3) if self.charge_baseline is not None else None
        charge_state = {
            "timestamp": ts,
            "charging_state": _strip_state(f.get("DetailedChargeState")) or _strip_state(f.get("ChargeState")),
            "battery_level": _round_int(f.get("Soc") if _num(f.get("Soc")) is not None else f.get("BatteryLevel")),
            "usable_battery_level": _round_int(f.get("BatteryLevel") if _num(f.get("BatteryLevel")) is not None else f.get("Soc")),
            "battery_range": _num(f.get("RatedRange")), "est_battery_range": _num(f.get("EstBatteryRange")),
            "ideal_battery_range": _num(f.get("IdealBatteryRange")), "charge_energy_added": energy_added,
            "charger_actual_current": _round_int(f.get("ChargeAmps")), "charger_phases": _round_int(f.get("ChargerPhases")),
            "charger_power": charger_power, "charger_voltage": _round_int(f.get("ChargerVoltage")),
            "conn_charge_cable": _strip_state(f.get("ChargingCableType")),
            "fast_charger_present": f.get("FastChargerPresent") if isinstance(f.get("FastChargerPresent"), bool) else None,
            "fast_charger_type": _strip_state(f.get("FastChargerType")), "time_to_full_charge": _num(f.get("TimeToFullCharge")),
            "charge_limit_soc": _round_int(f.get("ChargeLimitSoc")),
            "charge_current_request": _round_int(f.get("ChargeCurrentRequest")),
            "charge_current_request_max": _round_int(f.get("ChargeCurrentRequestMax")),
            "charge_port_door_open": f.get("ChargePortDoorOpen") if isinstance(f.get("ChargePortDoorOpen"), bool) else None,
            "battery_heater_on": f.get("BatteryHeaterOn") if isinstance(f.get("BatteryHeaterOn"), bool) else None,
            "not_enough_power_to_heat": f.get("NotEnoughPowerToHeat") if isinstance(f.get("NotEnoughPowerToHeat"), bool) else None}

        climate_state = {
            "timestamp": ts, "outside_temp": _num(f.get("OutsideTemp")), "inside_temp": _num(f.get("InsideTemp")),
            "is_climate_on": (_bool(f.get("HvacACEnabled")) or _strip_state(f.get("HvacPower")) == "On") or None,
            "climate_keeper_mode": (lambda m: m.lower() if m else None)(_strip_state(f.get("ClimateKeeperMode"))),
            "fan_status": _fan_speed(f.get("HvacFanStatus")), "driver_temp_setting": _num(f.get("HvacLeftTemperatureRequest")),
            "passenger_temp_setting": _num(f.get("HvacRightTemperatureRequest")),
            "is_preconditioning": f.get("PreconditioningEnabled") if isinstance(f.get("PreconditioningEnabled"), bool) else None,
            "is_front_defroster_on": _defrost_on(f.get("DefrostMode")),
            "is_rear_defroster_on": f.get("RearDefrostEnabled") if isinstance(f.get("RearDefrostEnabled"), bool) else None,
            "battery_heater": f.get("BatteryHeaterOn") if isinstance(f.get("BatteryHeaterOn"), bool) else None}

        sentry = _strip_state(f.get("SentryMode"))
        vehicle_state = {
            "timestamp": ts, "odometer": _num(f.get("Odometer")),
            "car_version": f.get("Version") if isinstance(f.get("Version"), str) else None,
            "locked": f.get("Locked") if isinstance(f.get("Locked"), bool) else None,
            "sentry_mode": (sentry in ("Armed", "On", "Enabled")) if sentry is not None else None,
            "tpms_pressure_fl": _num(f.get("TpmsPressureFl")), "tpms_pressure_fr": _num(f.get("TpmsPressureFr")),
            "tpms_pressure_rl": _num(f.get("TpmsPressureRl")), "tpms_pressure_rr": _num(f.get("TpmsPressureRr")),
            "fd_window": _window_state(f.get("FdWindow")), "fp_window": _window_state(f.get("FpWindow")),
            "rd_window": _window_state(f.get("RdWindow")), "rp_window": _window_state(f.get("RpWindow")),
            "is_user_present": False,
            "software_update": {"status": "", "download_perc": 0, "install_perc": 0, "version": ""}}
        doors = f.get("DoorState") if isinstance(f.get("DoorState"), dict) else None
        if doors is not None:
            vehicle_state.update({"df": 1 if doors.get("DriverFront") else 0, "pf": 1 if doors.get("PassengerFront") else 0,
                                  "dr": 1 if doors.get("DriverRear") else 0, "pr": 1 if doors.get("PassengerRear") else 0,
                                  "ft": 1 if doors.get("TrunkFront") else 0, "rt": 1 if doors.get("TrunkRear") else 0})

        return {**self.identity(), "state": "online", "drive_state": drive_state, "charge_state": charge_state,
                "climate_state": climate_state, "vehicle_state": vehicle_state, "vehicle_config": {}}

    def vehicle_data(self):
        tele = self._assemble()
        if self.prime:
            for sec in ("drive_state", "charge_state", "climate_state", "vehicle_state"):
                for k, v in (self.prime.get(sec) or {}).items():
                    if tele[sec].get(k) is None and v not in ("<invalid>", "invalid"):
                        tele[sec][k] = v
            if not tele.get("vehicle_config"):
                tele["vehicle_config"] = self.prime.get("vehicle_config") or {}
        if tele["charge_state"].get("charging_state") is None:
            tele["charge_state"]["charging_state"] = "Disconnected"
        if tele["drive_state"].get("power") is None:
            tele["drive_state"]["power"] = 0
        return tele


def _bool(v):
    return v if isinstance(v, bool) else False


class Manager:
    def __init__(self):
        self.lock = threading.Lock()
        self.vehicles = {}          # vin -> Vehicle
        self.refresh_token = ""     # rotated token (preferred over the config seed)
        self._load()

    def _load(self):
        try:
            with open(STATE_FILE) as fh:
                d = json.load(fh)
            self.refresh_token = d.get("refresh_token", "")
            for vin, vd in (d.get("vehicles") or {}).items():
                self.vehicles[vin] = Vehicle(vin, vd)
            print(f"[shim] warm-started: {len(self.vehicles)} vehicle(s)", flush=True)
        except (OSError, ValueError):
            print("[shim] no prior state; cold start", flush=True)

    def save(self):
        tmp = STATE_FILE + ".tmp"
        try:
            with self.lock:
                d = {"refresh_token": self.refresh_token,
                     "vehicles": {vin: v.dump() for vin, v in self.vehicles.items()}}
            with open(tmp, "w") as fh:
                json.dump(d, fh)
            os.replace(tmp, STATE_FILE)
        except OSError:
            pass

    def _vehicle(self, vin):
        v = self.vehicles.get(vin)
        if v is None:
            v = Vehicle(vin)
            self.vehicles[vin] = v
        return v

    def ingest(self, obj):
        if obj.get("msg") != "record_payload":
            return
        data = obj.get("data") or {}
        vin = obj.get("vin") or data.get("Vin")
        if not (isinstance(vin, str) and _VIN_RE.match(vin)):
            return
        with self.lock:
            self._vehicle(vin).ingest(data)

    def list(self):
        with self.lock:
            return [v.identity() for v in self.vehicles.values()]

    def by_ext_id(self, ext_id):
        with self.lock:
            for v in self.vehicles.values():
                if v.ext_id == ext_id:
                    return v
        return None

    # --- priming -----------------------------------------------------------
    def _wake(self, tid, at):
        """Wake a sleeping car and wait (~30s) for it to come online. Returns the resulting state.
        wake_up is exempt from Tesla's command-signing requirement; if Tesla still rejects it, we
        give up gracefully and the caller skips this car."""
        try:
            _http_post_bearer(FLEET_HOST + f"/api/1/vehicles/{tid}/wake_up", at)
        except Exception as e:
            print(f"[shim] prime: wake_up failed: {e}", flush=True)
            return "asleep"
        for _ in range(15):
            time.sleep(2)
            try:
                s = _http_get(FLEET_HOST + f"/api/1/vehicles/{tid}", at).get("response", {}).get("state")
            except Exception:
                s = None
            if s == "online":
                print("[shim] prime: car woke up", flush=True)
                return "online"
        print("[shim] prime: wake timed out", flush=True)
        return "asleep"

    def prime_once(self):
        rt = self.refresh_token or REFRESH_SEED
        if not (CLIENT_ID and rt):
            print("[shim] priming disabled (no client_id/refresh_token configured)", flush=True)
            return
        try:
            tok = _http_post_form(AUTH_HOST + "/oauth2/v3/token",
                                  {"grant_type": "refresh_token", "client_id": CLIENT_ID, "refresh_token": rt})
        except Exception as e:
            print(f"[shim] prime: token refresh failed: {e}", flush=True)
            return
        new_rt = tok.get("refresh_token")
        if new_rt and new_rt != self.refresh_token:
            with self.lock:
                self.refresh_token = new_rt
            self.save()
        at = tok.get("access_token")
        if not at:
            print("[shim] prime: no access_token returned", flush=True)
            return
        try:
            products = _http_get(FLEET_HOST + "/api/1/products", at).get("response", []) or []
        except Exception as e:
            print(f"[shim] prime: /products failed: {e}", flush=True)
            return
        primed = 0
        for p in products:
            vin = p.get("vin")
            if not (isinstance(vin, str) and _VIN_RE.match(vin)) or "vehicle_id" not in p:
                continue
            with self.lock:
                veh = self._vehicle(vin)
                veh.tesla_id = p.get("id")
                if p.get("display_name"):
                    veh.display_name = p["display_name"]
                tid, state = veh.tesla_id, p.get("state")
            if not tid:
                continue
            if state != "online":
                state = self._wake(tid, at) if WAKE_ON_PRIME else state
                if state != "online":
                    print(f"[shim] prime: {vin} is {state} — skipping vehicle_data", flush=True)
                    continue
            try:
                ep = "charge_state;climate_state;drive_state;location_data;vehicle_config;vehicle_state;gui_settings"
                vd = _http_get(FLEET_HOST + f"/api/1/vehicles/{tid}/vehicle_data?endpoints=" + urllib.parse.quote(ep), at)
                vd = vd.get("response", {})
            except Exception as e:
                print(f"[shim] prime: vehicle_data({vin}) failed: {e}", flush=True)
                continue
            with self.lock:
                veh.prime = {k: vd.get(k) for k in
                             ("drive_state", "charge_state", "climate_state", "vehicle_state", "vehicle_config")}
                veh.last_epoch = time.time()
            primed += 1
        self.save()
        print(f"[shim] primed {primed} online vehicle(s) from Tesla", flush=True)


MGR = Manager()


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
                            MGR.ingest(json.loads(line))
                        except (ValueError, KeyError):
                            pass
        except OSError:
            time.sleep(1.0)


def _saver():
    while True:
        time.sleep(SAVE_EVERY)
        MGR.save()


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

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:  # drain request body
            n = int(self.headers.get("Content-Length", "0") or 0)
            if n:
                self.rfile.read(n)
        except (ValueError, OSError):
            pass
        # OAuth token endpoint: lets TeslaMate run with NO real Tesla tokens — point its
        # TESLA_AUTH_HOST here and it "refreshes" against us forever. The token is opaque; the
        # "qts-" prefix makes TeslaMate skip JWT decoding. (The shim's own priming still uses the
        # real refresh token configured in the add-on options, calling real Tesla directly.)
        if path.endswith("/token"):
            self._json(200, {"access_token": "qts-shim-token", "token_type": "Bearer",
                             "expires_in": 28800, "refresh_token": "shim-refresh-token",
                             "created_at": int(time.time()), "id_token": "qts-shim-id-token"})
        else:
            self._json(404, {"error": "not_found"})

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/api/1/products", "/api/1/vehicles", "/api/1/vehicles/"):
            lst = MGR.list()
            self._json(200, {"response": lst, "count": len(lst)})
            return
        m = _PATH_DATA.match(path)
        if m:
            v = MGR.by_ext_id(int(m.group(1)))
            if v is None:
                self._json(404, {"error": "not_found"})
            elif not v.ready():
                self._json(408, {"error": "vehicle unavailable: data not ready", "error_description": ""})
            else:
                self._json(200, {"response": v.vehicle_data()})
            return
        m = _PATH_ONE.match(path)
        if m:
            v = MGR.by_ext_id(int(m.group(1)))
            self._json(200 if v else 404, {"response": v.identity()} if v else {"error": "not_found"})
            return
        self._json(404, {"error": "not_found"})

    do_HEAD = do_GET


def main():
    threading.Thread(target=_tail, daemon=True).start()
    threading.Thread(target=_saver, daemon=True).start()
    threading.Thread(target=MGR.prime_once, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[shim] Fleet-API shim listening on :{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
