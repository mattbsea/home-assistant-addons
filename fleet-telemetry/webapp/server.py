#!/usr/bin/env python3
"""Tiny stdlib web dashboard for the Tesla Fleet Telemetry add-on.

Tails the fleet-telemetry logger output (JSON lines written to RECORDS_FILE by `tee`),
keeps the latest value per telemetry field per VIN plus a little history, and serves an
ingress dashboard. Read-only and isolated: if this process dies, the telemetry server is
unaffected.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import ssl
import urllib.error
import urllib.parse
import urllib.request

_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RECORDS_FILE = os.environ.get("FT_RECORDS_FILE", "/tmp/ft-records.jsonl")
CERT_FILE = os.environ.get("FT_CERT_FILE", "/data/certs/server.crt")
PORT = int(os.environ.get("FT_WEB_PORT", "8099"))
# The shim's persisted snapshot (telemetry + a real-Fleet-API "prime"); we read it so the dashboard
# shows the same complete picture the shim serves TeslaMate, even for fields telemetry doesn't stream.
SHIM_STATE_FILE = os.environ.get("FT_SHIM_STATE", "/data/shim-state.json")
NAMESPACE = os.environ.get("FT_NAMESPACE", "tesla_telemetry")
ADDON_VERSION = os.environ.get("FT_ADDON_VERSION", "")
WIZARD_STATE_FILE = os.environ.get("FT_WIZARD_STATE", "/data/wizard-state.json")
HISTORY_MAX = 600  # ~ last N samples kept per series for sparklines

START_TIME = time.time()
_META = {"CreatedAt", "IsResend", "Vin"}

# ---------------------------------------------------------------------------
# Shared state (guarded by _lock)
# ---------------------------------------------------------------------------
_lock = threading.Lock()
# vin -> { field -> {value, created_at, received_at} }
_latest = defaultdict(dict)
# vin -> { "soc": deque[(ts, val)], "speed": deque[(ts, val)] }
_history = defaultdict(lambda: {"soc": deque(maxlen=HISTORY_MAX),
                                "speed": deque(maxlen=HISTORY_MAX)})
_record_times = deque(maxlen=5000)   # epoch seconds of every record, for rate stats
_total_records = 0
_client_versions = {}                # vin -> device_client_version
_last_record_epoch = 0.0


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_location(val):
    """Return (lat, lon) from a Location field value in whatever shape it arrives."""
    if isinstance(val, dict):
        lat = val.get("latitude", val.get("Latitude"))
        lon = val.get("longitude", val.get("Longitude"))
        if lat is not None and lon is not None:
            return _num(lat), _num(lon)
    if isinstance(val, str) and "," in val:
        parts = val.split(",")
        if len(parts) == 2:
            return _num(parts[0]), _num(parts[1])
    return None, None


def _ingest(obj):
    global _total_records, _last_record_epoch
    if obj.get("msg") != "record_payload":
        return
    data = obj.get("data") or {}
    vin = obj.get("vin") or data.get("Vin") or "unknown"
    # Validate VIN format; fall back to a safe label rather than trusting arbitrary input.
    if not (isinstance(vin, str) and _VIN_RE.match(vin)):
        vin = "unknown"
    created = data.get("CreatedAt", "")
    now = time.time()
    meta = obj.get("metadata") or {}
    with _lock:
        _total_records += 1
        _last_record_epoch = now
        _record_times.append(now)
        if meta.get("device_client_version"):
            _client_versions[vin] = meta["device_client_version"]
        for key, value in data.items():
            if key in _META:
                continue
            _latest[vin][key] = {"value": value, "created_at": created, "received_at": now}
            if key == "Soc":
                n = _num(value)
                if n is not None:
                    _history[vin]["soc"].append((now, n))
            elif key == "VehicleSpeed":
                n = _num(value)
                if n is not None:
                    _history[vin]["speed"].append((now, n))


def _tail_records():
    """Follow RECORDS_FILE, tolerating truncation/rotation and the file not existing yet."""
    pos = 0
    while True:
        try:
            if not os.path.exists(RECORDS_FILE):
                time.sleep(1.0)
                continue
            with open(RECORDS_FILE, "r", errors="replace") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                if size < pos:           # truncated/rotated -> restart from top
                    pos = 0
                fh.seek(pos)
                while True:
                    line = fh.readline()
                    if not line:
                        pos = fh.tell()
                        # detect rotation: file shrank
                        try:
                            if os.path.getsize(RECORDS_FILE) < pos:
                                break
                        except OSError:
                            break
                        time.sleep(0.5)
                        continue
                    line = line.strip()
                    if not line or line[0] != "{":
                        continue
                    try:
                        _ingest(json.loads(line))
                    except (ValueError, KeyError):
                        pass
        except OSError:
            time.sleep(1.0)


def _cert_expiry():
    try:
        out = subprocess.run(["openssl", "x509", "-enddate", "-noout", "-in", CERT_FILE],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and "notAfter=" in out.stdout:
            s = out.stdout.strip().split("notAfter=", 1)[1]
            exp = time.mktime(time.strptime(s, "%b %d %H:%M:%S %Y %Z"))
            days = (exp - time.time()) / 86400.0
            return {"not_after": s, "days_left": round(days, 1)}
    except Exception:
        pass
    return {"not_after": None, "days_left": None}


def _rate_per_min():
    now = time.time()
    with _lock:
        recent = [t for t in _record_times if now - t <= 600]
    if not recent:
        return 0.0
    span = max(now - recent[0], 1.0)
    return round(len(recent) / span * 60.0, 1)


def _prime_to_fields(p):
    """Map a shim 'prime' (Tesla vehicle_data shape) back to telemetry field names so the dashboard's
    existing cards can render it. Used only to fill gaps the live stream hasn't (or won't) provide."""
    ds = p.get("drive_state") or {}; cs = p.get("charge_state") or {}
    cl = p.get("climate_state") or {}; vs = p.get("vehicle_state") or {}; vc = p.get("vehicle_config") or {}
    out = {}

    def put(k, v):
        if v is not None:
            out[k] = v
    put("Soc", cs.get("battery_level"))
    put("BatteryLevel", cs.get("usable_battery_level"))
    put("RatedRange", cs.get("battery_range"))
    put("EstBatteryRange", cs.get("est_battery_range"))
    put("IdealBatteryRange", cs.get("ideal_battery_range"))
    put("DetailedChargeState", cs.get("charging_state"))
    put("ChargerVoltage", cs.get("charger_voltage"))
    put("ChargeAmps", cs.get("charger_actual_current"))
    put("TimeToFullCharge", cs.get("time_to_full_charge"))
    put("ChargingCableType", cs.get("conn_charge_cable"))
    put("FastChargerType", cs.get("fast_charger_type"))
    put("ChargeLimitSoc", cs.get("charge_limit_soc"))
    put("ChargerPhases", cs.get("charger_phases"))
    if isinstance(cs.get("fast_charger_present"), bool):
        put("FastChargerPresent", cs["fast_charger_present"])
    put("ChargeCurrentRequest", cs.get("charge_current_request"))
    put("ChargeCurrentRequestMax", cs.get("charge_current_request_max"))
    if isinstance(cs.get("charge_port_door_open"), bool):
        put("ChargePortDoorOpen", cs["charge_port_door_open"])
    if isinstance(cs.get("battery_heater_on"), bool):
        put("BatteryHeaterOn", cs["battery_heater_on"])
    if isinstance(cs.get("not_enough_power_to_heat"), bool):
        put("NotEnoughPowerToHeat", cs["not_enough_power_to_heat"])
    put("InsideTemp", cl.get("inside_temp"))
    put("OutsideTemp", cl.get("outside_temp"))
    put("ClimateKeeperMode", cl.get("climate_keeper_mode"))
    if isinstance(cl.get("is_climate_on"), bool):
        put("HvacACEnabled", cl["is_climate_on"])
    if isinstance(cl.get("is_preconditioning"), bool):
        put("PreconditioningEnabled", cl["is_preconditioning"])
    if isinstance(cl.get("is_rear_defroster_on"), bool):
        put("RearDefrostEnabled", cl["is_rear_defroster_on"])
    if isinstance(cl.get("battery_heater"), bool):
        put("BatteryHeaterOn", cl["battery_heater"])
    fs = cl.get("fan_status")
    if fs is not None:
        try:
            put("HvacFanStatus", int(fs))
        except (TypeError, ValueError):
            pass
    put("HvacLeftTemperatureRequest", cl.get("driver_temp_setting"))
    put("HvacRightTemperatureRequest", cl.get("passenger_temp_setting"))
    if ds.get("latitude") is not None and ds.get("longitude") is not None:
        put("Location", {"latitude": ds["latitude"], "longitude": ds["longitude"]})
    put("GpsHeading", ds.get("heading"))
    put("VehicleSpeed", ds.get("speed"))
    put("Gear", ds.get("shift_state"))
    put("Destination", ds.get("active_route_destination"))
    put("MilesToArrival", ds.get("active_route_miles_to_arrival"))
    put("MinutesToArrival", ds.get("active_route_minutes_to_arrival"))
    put("Odometer", vs.get("odometer"))
    put("Version", vs.get("car_version"))
    if isinstance(vs.get("locked"), bool):
        put("Locked", vs["locked"])
    if isinstance(vs.get("sentry_mode"), bool):
        put("SentryMode", "Armed" if vs["sentry_mode"] else "Off")
    for a, b in (("tpms_pressure_fl", "TpmsPressureFl"), ("tpms_pressure_fr", "TpmsPressureFr"),
                 ("tpms_pressure_rl", "TpmsPressureRl"), ("tpms_pressure_rr", "TpmsPressureRr")):
        put(b, vs.get(a))
    if any(k in vs for k in ("df", "pf", "dr", "pr", "ft", "rt")):
        put("DoorState", {"DriverFront": bool(vs.get("df")), "PassengerFront": bool(vs.get("pf")),
                          "DriverRear": bool(vs.get("dr")), "PassengerRear": bool(vs.get("pr")),
                          "TrunkFront": bool(vs.get("ft")), "TrunkRear": bool(vs.get("rt"))})
    for a, b in (("fd_window", "FdWindow"), ("fp_window", "FpWindow"),
                 ("rd_window", "RdWindow"), ("rp_window", "RpWindow")):
        if vs.get(a) is not None:
            put(b, vs[a])
    put("CarType", vc.get("car_type"))
    put("Trim", vc.get("trim_badging"))
    put("ExteriorColor", vc.get("exterior_color"))
    put("Wheels", vc.get("wheel_type"))
    return out


def _load_primes():
    try:
        with open(SHIM_STATE_FILE) as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {vin: {"prime": vd.get("prime"), "display_name": vd.get("display_name")}
            for vin, vd in (d.get("vehicles") or {}).items()}


def _load_wizard_state():
    try:
        with open(WIZARD_STATE_FILE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_wizard_state(data):
    dirpath = os.path.dirname(WIZARD_STATE_FILE) or "."
    fd, tmp = tempfile.mkstemp(dir=dirpath, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, WIZARD_STATE_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _check_pubkey(domain):
    import ipaddress
    import socket
    domain = domain.strip().lower().rstrip(".")
    # Only valid hostname characters — no path, port, userinfo, or scheme
    if not re.match(r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?)*$", domain):
        return {"ok": False, "error": "Invalid domain — must be a plain hostname like telemetry.example.org"}
    # Resolve and reject private/loopback addresses
    try:
        addrs = socket.getaddrinfo(domain, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return {"ok": False, "error": "Domain does not resolve — check DNS"}
    for _, _, _, _, addr in addrs:
        try:
            ip = ipaddress.ip_address(addr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return {"ok": False, "error": "Domain must resolve to a public IP address"}
        except ValueError:
            pass
    url = f"https://{domain}/.well-known/appspecific/com.tesla.3p.public-key.pem"
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.urlopen(url, context=ctx, timeout=10)
        body = req.read(4096).decode("utf-8", errors="replace")
        if "BEGIN PUBLIC KEY" in body or "BEGIN EC PUBLIC KEY" in body:
            return {"ok": True, "url": url}
        return {"ok": False, "error": "URL reachable but content is not an EC public key PEM"}
    except Exception as exc:
        return {"ok": False, "error": f"Could not fetch public key: {exc}"}


def _check_cert_detail():
    try:
        out = subprocess.run(
            ["openssl", "x509", "-noout", "-subject", "-enddate", "-in", CERT_FILE],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return {"ok": False, "error": out.stderr.strip() or "certificate file not found or invalid"}
        subject = ""
        not_after = ""
        for line in out.stdout.splitlines():
            if line.startswith("subject="):
                subject = line.split("=", 1)[1].strip()
            elif line.startswith("notAfter="):
                not_after = line.split("=", 1)[1].strip()
        days = None
        if not_after:
            try:
                exp = time.mktime(time.strptime(not_after, "%b %d %H:%M:%S %Y %Z"))
                days = round((exp - time.time()) / 86400.0, 1)
            except ValueError:
                pass
        ok = days is not None and days > 0
        return {"ok": ok, "subject": subject, "not_after": not_after, "days_left": days}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _check_records_status():
    with _lock:
        total = _total_records
        last = _last_record_epoch
        vins = list(_latest.keys())
    return {
        "ok": total > 0,
        "total": total,
        "last_epoch": last,
        "vins": vins,
    }


_FLEET_HOSTS = {
    "na": "https://fleet-api.prd.na.vn.cloud.tesla.com",
    "eu": "https://fleet-api.prd.eu.vn.cloud.tesla.com",
    "cn": "https://fleet-api.prd.cn.vn.cloud.tesla.com",
}
_TELEMETRY_FIELDS = {
    "VehicleSpeed":              {"interval_seconds": 10},
    "Location":                  {"interval_seconds": 30},
    "GpsHeading":                {"interval_seconds": 10},
    "Soc":                       {"interval_seconds": 30},
    "BatteryLevel":              {"interval_seconds": 30},
    "Gear":                      {"interval_seconds": 5},
    "PackVoltage":               {"interval_seconds": 10},
    "PackCurrent":               {"interval_seconds": 10},
    "RatedRange":                {"interval_seconds": 60},
    "EstBatteryRange":           {"interval_seconds": 60},
    "IdealBatteryRange":         {"interval_seconds": 60},
    "DetailedChargeState":       {"interval_seconds": 30},
    "ACChargingPower":           {"interval_seconds": 30},
    "DCChargingPower":           {"interval_seconds": 30},
    "ACChargingEnergyIn":        {"interval_seconds": 30},
    "DCChargingEnergyIn":        {"interval_seconds": 30},
    "ChargeAmps":                {"interval_seconds": 30},
    "ChargerVoltage":            {"interval_seconds": 30},
    "ChargerPhases":             {"interval_seconds": 60},
    "ChargeLimitSoc":            {"interval_seconds": 60},
    "TimeToFullCharge":          {"interval_seconds": 60},
    "ChargingCableType":         {"interval_seconds": 60},
    "FastChargerPresent":        {"interval_seconds": 60},
    "FastChargerType":           {"interval_seconds": 60},
    "ChargeCurrentRequest":      {"interval_seconds": 30},
    "ChargeCurrentRequestMax":   {"interval_seconds": 60},
    "ChargePortDoorOpen":        {"interval_seconds": 30},
    "BatteryHeaterOn":           {"interval_seconds": 30},
    "NotEnoughPowerToHeat":      {"interval_seconds": 30},
    "InsideTemp":                {"interval_seconds": 60},
    "OutsideTemp":               {"interval_seconds": 60},
    "HvacACEnabled":             {"interval_seconds": 60},
    "HvacPower":                 {"interval_seconds": 60},
    "HvacFanStatus":             {"interval_seconds": 60},
    "HvacLeftTemperatureRequest":  {"interval_seconds": 60},
    "HvacRightTemperatureRequest": {"interval_seconds": 60},
    "ClimateKeeperMode":         {"interval_seconds": 60},
    "PreconditioningEnabled":    {"interval_seconds": 30},
    "DefrostMode":               {"interval_seconds": 30},
    "RearDefrostEnabled":        {"interval_seconds": 30},
    "Odometer":                  {"interval_seconds": 60},
    "Version":                   {"interval_seconds": 3600},
    "Locked":                    {"interval_seconds": 60},
    "SentryMode":                {"interval_seconds": 60},
    "DoorState":                 {"interval_seconds": 60},
    "FdWindow":                  {"interval_seconds": 60},
    "FpWindow":                  {"interval_seconds": 60},
    "RdWindow":                  {"interval_seconds": 60},
    "RpWindow":                  {"interval_seconds": 60},
    "TpmsPressureFl":            {"interval_seconds": 300},
    "TpmsPressureFr":            {"interval_seconds": 300},
    "TpmsPressureRl":            {"interval_seconds": 300},
    "TpmsPressureRr":            {"interval_seconds": 300},
    "DestinationName":                       {"interval_seconds": 30},
    "DestinationLocation":                   {"interval_seconds": 30},
    "MilesToArrival":                        {"interval_seconds": 30},
    "MinutesToArrival":                      {"interval_seconds": 30},
    "RouteLastUpdated":                      {"interval_seconds": 30},
    "RouteTrafficMinutesDelay":              {"interval_seconds": 30},
    "ExpectedEnergyPercentAtTripArrival":    {"interval_seconds": 30},
}


def _send_telemetry_config(domain, region):
    # fleet_telemetry_config is a signed vehicle command — it requires the app EC private key via
    # tesla-http-proxy. A plain Bearer-token POST always returns a generic 404.
    client_id = os.environ.get("FT_SHIM_CLIENT_ID", "")
    if not client_id:
        return {"ok": False, "error": "teslamate_shim_client_id must be set in add-on options"}

    # Load shim state once for both the refresh token and the VIN list.
    state = {}
    try:
        with open(SHIM_STATE_FILE) as fh:
            state = json.load(fh)
    except (OSError, IOError, json.JSONDecodeError):
        pass

    rt = state.get("refresh_token", "") or os.environ.get("FT_SHIM_REFRESH_TOKEN", "")
    if not rt:
        return {"ok": False, "error": "No refresh token available — set teslamate_shim_refresh_token in add-on options"}

    vins = list(state.get("vehicles", {}).keys())
    if not vins:
        return {"ok": False, "error": "No VINs in shim state — wait for the shim to prime after add-on start, then retry"}

    auth_host = os.environ.get("FT_SHIM_AUTH_HOST", "https://auth.tesla.com")

    # Obtain user-context access token via refresh_token grant.
    try:
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": rt,
        }).encode()
        req = urllib.request.Request(
            auth_host + "/oauth2/v3/token", data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            tok = json.load(r)
    except urllib.error.HTTPError as e:
        try:
            body2 = e.read(1024).decode("utf-8", errors="replace")
        except Exception:
            body2 = ""
        return {"ok": False, "error": f"Token request failed HTTP {e.code}: {body2[:300]}"}
    except Exception as e:
        return {"ok": False, "error": f"Token request failed: {e}"}

    at = tok.get("access_token")
    if not at:
        return {"ok": False, "error": f"No access_token returned: {tok}"}

    # Persist the rotated refresh token so future calls use the current one.
    new_rt = tok.get("refresh_token")
    if new_rt and new_rt != rt:
        try:
            state["refresh_token"] = new_rt
            tmp = SHIM_STATE_FILE + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(state, fh)
            os.replace(tmp, SHIM_STATE_FILE)
        except Exception:
            pass

    # Build the fleet_telemetry_config payload (CA chain from the add-on's TLS cert).
    ca_chain = ""
    try:
        with open(CERT_FILE) as fh:
            pem = fh.read()
        certs = re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", pem, re.DOTALL)
        if len(certs) > 1:
            ca_chain = "\n".join(certs[1:])
    except (OSError, IOError):
        pass
    config = {"hostname": domain, "port": 443, "ca": ca_chain, "fields": _TELEMETRY_FIELDS}

    # The app private key signs the JWS payload inside tesla-http-proxy.
    key_file = "/share/tesla-fleet/private-key.pem"
    if not os.path.exists(key_file):
        return {"ok": False, "error": f"App private key not found at {key_file} — place your EC private key there"}

    # Generate an ephemeral TLS cert for the local proxy (must differ from the app signing key).
    tmpdir = tempfile.mkdtemp(prefix="ft-proxy-")
    proxy_key = os.path.join(tmpdir, "proxy.key")
    proxy_cert = os.path.join(tmpdir, "proxy.crt")
    try:
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "ec",
             "-pkeyopt", "ec_paramgen_curve:P-256",
             "-keyout", proxy_key, "-out", proxy_cert,
             "-days", "1", "-nodes", "-subj", "/CN=127.0.0.1",
             "-addext", "subjectAltName=IP:127.0.0.1"],
            check=True, capture_output=True, timeout=15,
        )
    except Exception as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return {"ok": False, "error": f"Failed to generate proxy TLS cert: {e}"}

    # Find a free local port.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        proxy_port = s.getsockname()[1]

    proxy_proc = None
    try:
        proxy_proc = subprocess.Popen(
            ["/usr/local/bin/tesla-http-proxy",
             "-key-file", key_file,
             "-cert", proxy_cert,
             "-tls-key", proxy_key,
             "-port", str(proxy_port),
             "-host", "localhost"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Wait up to 10 s for the proxy to start accepting connections.
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", proxy_port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            return {"ok": False, "error": "tesla-http-proxy did not start within 10 seconds"}

        ssl_ctx = ssl.create_default_context()
        ssl_ctx.load_verify_locations(proxy_cert)

        payload = json.dumps({"vins": vins, "config": config}).encode()
        req = urllib.request.Request(
            f"https://127.0.0.1:{proxy_port}/api/1/vehicles/fleet_telemetry_config",
            data=payload, method="POST",
            headers={
                "Authorization": "Bearer " + at,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as r:
                resp = json.load(r)
            return {"ok": True, "vins": vins, "response": resp}
        except urllib.error.HTTPError as e:
            try:
                body2 = e.read(2048).decode("utf-8", errors="replace")
            except Exception:
                body2 = ""
            return {"ok": False, "error": f"fleet_telemetry_config failed HTTP {e.code}: {body2[:500]}"}
        except Exception as e:
            return {"ok": False, "error": f"fleet_telemetry_config request failed: {e}"}
    finally:
        if proxy_proc and proxy_proc.poll() is None:
            proxy_proc.kill()
        shutil.rmtree(tmpdir, ignore_errors=True)


def build_state():
    now = time.time()
    with _lock:
        vins = list(_latest.keys())
        latest = {v: dict(f) for v, f in _latest.items()}
        history = {v: {"soc": list(h["soc"]), "speed": list(h["speed"])}
                   for v, h in _history.items()}
        total = _total_records
        client_versions = dict(_client_versions)
        last_epoch = _last_record_epoch
    primes = _load_primes()
    vins = list(dict.fromkeys(list(vins) + list(primes.keys())))  # union: telemetry + primed VINs
    vehicles = []
    for vin in vins:
        fields = dict(latest.get(vin, {}))
        # Fill display gaps from the shim's prime (live telemetry, having a real received_at, wins).
        pe = primes.get(vin) or {}
        if pe.get("prime"):
            for k, v in _prime_to_fields(pe["prime"]).items():
                if k not in fields:
                    fields[k] = {"value": v, "created_at": "", "received_at": 0}
        loc_lat = loc_lon = None
        if "Location" in fields:
            loc_lat, loc_lon = _parse_location(fields["Location"]["value"])
        last_seen = max((f["received_at"] for f in fields.values()), default=0)
        # A vehicle can appear in _latest (e.g. a Location-only record) before it has any
        # Soc/VehicleSpeed history, so _history may not have this vin yet — default safely.
        hist = history.get(vin) or {"soc": [], "speed": []}
        vehicles.append({
            "vin": vin,
            "display_name": pe.get("display_name") or vin,
            "fields": fields,
            "location": {"lat": loc_lat, "lon": loc_lon},
            "soc_history": [round(v, 2) for _, v in hist["soc"]],
            "speed_history": [round(v, 2) for _, v in hist["speed"]],
            "client_version": client_versions.get(vin),
            "last_seen_epoch": last_seen,
            "online": (now - last_seen) < 600 if last_seen else False,
        })
    return {
        "now": now,
        "uptime_seconds": int(now - START_TIME),
        "total_records": total,
        "records_per_min": _rate_per_min(),
        "last_record_epoch": last_epoch,
        "namespace": NAMESPACE,
        "version": ADDON_VERSION,
        "cert": _cert_expiry(),
        "vehicles": vehicles,
    }


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        if path.endswith("/api/state"):
            self._send(200, json.dumps(build_state()))
        elif path.endswith("/api/wizard/state"):
            self._send(200, json.dumps(_load_wizard_state()))
        elif path.endswith("/setup"):
            self._send(200, PAGE_SETUP, "text/html; charset=utf-8")
        elif path == "" or path.endswith("/index.html"):
            try:
                with open(WIZARD_STATE_FILE) as fh:
                    ws = json.load(fh)
                redirect = not ws.get("completed")
            except FileNotFoundError:
                redirect = True   # first run — no state file yet
            except (OSError, ValueError):
                redirect = False  # broken file → serve dashboard, don't trap user in redirect loop
            if redirect:
                self.send_response(302)
                self.send_header("Location", "./setup")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
            else:
                self._send(200, PAGE, "text/html; charset=utf-8")
        else:
            self._send(200, PAGE, "text/html; charset=utf-8")

    do_HEAD = do_GET

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n > 0 else b""
        except (ValueError, OSError):
            body = b""
        try:
            payload = json.loads(body) if body else {}
        except ValueError:
            return self._send(400, json.dumps({"error": "invalid json"}))

        if path.endswith("/api/wizard/save"):
            if not isinstance(payload, dict):
                return self._send(400, json.dumps({"error": "payload must be a JSON object"}))
            state = _load_wizard_state()
            # Deep-merge: top-level keys in payload overwrite state; nested dicts merged
            for k, v in payload.items():
                if isinstance(v, dict) and isinstance(state.get(k), dict):
                    state[k] = {**state[k], **v}
                else:
                    state[k] = v
            _save_wizard_state(state)
            self._send(200, json.dumps({"ok": True}))
        elif path.endswith("/api/wizard/check"):
            check = payload.get("check")
            if check == "pubkey":
                domain = str(payload.get("domain", "")).strip()
                domain = re.sub(r'^https?://', '', domain)
                domain = domain.split("/")[0]  # drop any path component
                if not domain:
                    return self._send(400, json.dumps({"error": "domain required"}))
                self._send(200, json.dumps(_check_pubkey(domain)))
            elif check == "cert":
                self._send(200, json.dumps(_check_cert_detail()))
            elif check == "records":
                self._send(200, json.dumps(_check_records_status()))
            elif check == "send_telemetry_config":
                domain = str(payload.get("domain", "")).strip().lstrip("https://").lstrip("http://").split("/")[0]
                region = str(payload.get("region", "na")).strip()
                self._send(200, json.dumps(_send_telemetry_config(domain, region)))
            else:
                self._send(400, json.dumps({"error": "unknown check type"}))
        else:
            self._send(404, json.dumps({"error": "not found"}))


PAGE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fleet Telemetry</title>
<style>
:root{--bg:#0b0f17;--card:#141b29;--card2:#1b2435;--line:#26314a;--txt:#e7edf7;--mut:#8a98b3;--accent:#3ea6ff;--good:#3ddc97;--warn:#ffb454;--bad:#ff5d5d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:18px}
header{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:16px}
h1{font-size:18px;margin:0;font-weight:650}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block}
.pill{display:inline-flex;align-items:center;gap:7px;background:var(--card);border:1px solid var(--line);border-radius:999px;padding:5px 12px;font-size:12.5px;color:var(--mut)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;min-height:120px}
.card h3{margin:0 0 10px;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--mut);font-weight:600}
.big{font-size:34px;font-weight:680;line-height:1}
.unit{font-size:15px;color:var(--mut);font-weight:500;margin-left:4px}
.sub{color:var(--mut);font-size:12.5px;margin-top:8px}
.battery{position:relative;height:14px;background:var(--card2);border-radius:7px;overflow:hidden;margin:12px 0 4px}
.battery>span{position:absolute;left:0;top:0;bottom:0;border-radius:7px;transition:width .6s}
.gear{display:flex;gap:6px;margin-top:6px}
.gear b{width:34px;height:38px;display:flex;align-items:center;justify-content:center;border-radius:8px;background:var(--card2);color:var(--mut);font-weight:700;font-size:16px}
.gear b.on{background:var(--accent);color:#04121f}
.kv{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--line);font-size:13px}
.kv:last-child{border-bottom:0}.kv span:first-child{color:var(--mut)}
.kv i{font-style:normal;color:var(--mut);font-size:11px;margin-left:3px}
@keyframes kvpulse{0%,8%{color:var(--good);font-weight:700;font-size:14.5px}100%{color:inherit;font-weight:inherit;font-size:inherit}}
.kv-pulse>span:last-child{animation:kvpulse 5s ease-out forwards}
h2{font-size:15px;margin:18px 0 10px}
.spark{width:100%;height:46px;display:block;margin-top:8px}
a{color:var(--accent)}
iframe{width:100%;height:200px;border:0;border-radius:10px;margin-top:6px;background:var(--card2)}
.foot{color:var(--mut);font-size:12px;margin-top:18px;text-align:center}
.muted{color:var(--mut)}
</style></head>
<body><div class="wrap">
<header>
  <h1>⚡ Tesla Telemetry</h1>
  <span class="pill" id="verPill">v—</span>
  <span class="pill"><span class="dot" id="statusDot"></span><span id="statusTxt">connecting…</span></span>
  <span class="pill" id="ratePill">— rec/min</span>
  <span class="pill" id="totalPill">— records</span>
  <a class="pill" href="./setup" style="text-decoration:none;color:var(--mut)">⚙ Setup Guide</a>
  <button class="pill" id="unitBtn" onclick="toggleUnits()" style="cursor:pointer;border:none;background:var(--card2)"></button>
  <span style="flex:1"></span>
  <span class="pill" id="updatedPill">updated —</span>
</header>
<div id="content">
  <p id="empty" class="muted">Waiting for the first telemetry record… (the vehicle streams every few minutes when awake)</p>
  <div id="vehicles"></div>
</div>
<div class="foot" id="foot"></div>
</div>
<script>
const $=s=>document.querySelector(s);
const esc=s=>String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function ago(epoch){if(!epoch)return"never";const s=Math.max(0,Date.now()/1000-epoch);
 if(s<60)return Math.round(s)+"s ago";if(s<3600)return Math.round(s/60)+"m ago";
 if(s<86400)return Math.round(s/3600)+"h ago";return Math.round(s/86400)+"d ago";}
function dur(s){const d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);
 return (d?d+"d ":"")+(h?h+"h ":"")+m+"m";}
function fmt(n,dp=0){return n==null?"—":Number(n).toLocaleString(undefined,{maximumFractionDigits:dp});}
function spark(vals,color){if(!vals||vals.length<2)return"";const w=240,h=46,mn=Math.min(...vals),mx=Math.max(...vals),rg=(mx-mn)||1;
 const pts=vals.map((v,i)=>[i/(vals.length-1)*w,h-4-((v-mn)/rg)*(h-8)]);
 const d="M"+pts.map(p=>p[0].toFixed(1)+","+p[1].toFixed(1)).join(" L");
 return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><path d="${d}" fill="none" stroke="${color}" stroke-width="2"/></svg>`;}
function batColor(p){return p>50?"var(--good)":p>20?"var(--warn)":"var(--bad)";}
const GEARS=["P","R","N","D"];
function gearName(v){if(v==null)return null;const s=String(v).toUpperCase();const last=s.slice(-1);
 if("PRND".includes(last))return last; // handles "DriveGearP", "P", etc.
 if(s.includes("PARK"))return"P";if(s.includes("REV"))return"R";
 if(s.includes("NEUT"))return"N";if(s.includes("DRIVE"))return"D";return s;}
function card(t,inner){return `<div class="card"><h3>${t}</h3>${inner}</div>`;}

// Persistent per-VIN map state: the last lat,lon we pointed the iframe at, so we only
// reload the OpenStreetMap embed when the vehicle actually moves (not every refresh).
const mapKeys={};
const prevKV={}; // vin -> {label -> valueText}, used to detect changed fields for pulse animation
let useImperial=localStorage.getItem('ft_units')!=='metric';
document.getElementById('unitBtn').textContent=useImperial?'°F · mi · psi':'°C · km · bar';
function toggleUnits(){
  useImperial=!useImperial;
  localStorage.setItem('ft_units',useImperial?'imperial':'metric');
  document.getElementById('unitBtn').textContent=useImperial?'°F · mi · psi':'°C · km · bar';
  tick();
}
// Telemetry enums arrive verbose ("DetailedChargeStateDisconnected", "WindowStateClosed",
// "SettingTemperatureUnitFahrenheit"). Strip the prefix to the meaningful suffix, and treat the
// "<invalid>" sentinel (field not applicable right now) as absent.
function pretty(field,val){
 if(val==null)return null;
 if(typeof val!=="string")return val;
 if(val==="<invalid>"||val==="invalid"||val==="")return null;
 let s=val;const i=s.lastIndexOf("State");
 if(i>=0&&i+5<s.length){s=s.slice(i+5);}
 else{for(const p of [field,field.replace(/^Setting/,"")]){if(p&&s.startsWith(p)&&s.length>p.length){s=s.slice(p.length);break;}}}
 return s;
}
function buildCards(v){
 const f=v.fields||{};
 const raw=k=>f[k]?f[k].value:undefined;
 const N=k=>{const x=raw(k);if(x==null||x==="<invalid>")return null;const n=Number(x);return Number.isNaN(n)?null:n;};
 const S=k=>pretty(k,raw(k));
 const B=k=>{const x=raw(k);return (x===true||x===false)?x:null;};
 const nf=(x,dp=0)=>x==null?null:Number(x).toLocaleString(undefined,{maximumFractionDigits:dp});
 const row=(label,val,unit)=>{if(val==null||val===""||val==="<invalid>")return"";
   return `<div class="kv"><span>${esc(label)}</span><span>${esc(String(val))}${unit?` <i>${esc(unit)}</i>`:""}</span></div>`;};
 const mcard=(t,inner)=>inner&&inner.trim()?card(t,inner):"";
 const fahr=useImperial;
 const tc=k=>{const n=N(k);return n==null?null:(fahr?Math.round(n*9/5+32):Math.round(n*10)/10);};
 const tu=fahr?"°F":"°C";
 const distUnit=useImperial?"mi":"km";
 const d=v=>v==null?null:(useImperial?v:Math.round(v*1.60934*10)/10);
 const pressUnit=useImperial?"psi":"bar";
 const onoff=k=>{const b=B(k);return b==null?null:(b?"on":"off");};
 let cards="";

 // Battery (headline + range/energy)
 const soc=N('Soc')!=null?N('Soc'):N('BatteryLevel');
 const sh=v.soc_history||[];
 const charging=S('ChargeState')==="Charging"||(N('ACChargingPower')||0)>0||(N('DCChargingPower')||0)>0;
 cards+=card("Battery"+(charging?" ⚡":""),
   `<div class="big">${soc==null?"—":fmt(soc,0)}<span class="unit">%</span></div>`
   +`<div class="battery"><span style="width:${soc==null?0:Math.max(2,soc)}%;background:${batColor(soc||0)}"></span></div>`
   +spark(sh,batColor(soc||0))
   +row("Range",nf(d(N('RatedRange')!=null?N('RatedRange'):N('IdealBatteryRange'))),distUnit)
   +row("Energy left",nf(N('EnergyRemaining'),1),"kWh")
   +row("Charge limit",nf(N('ChargeLimitSoc')),"%"));

 // Charging — full detail only while actually charging; otherwise compact state.
 const chState=S('DetailedChargeState')||S('ChargeState');
 let chInner=row("State",chState);
 if(charging){
   chInner+=row("Power",nf((N('ACChargingPower')||0)+(N('DCChargingPower')||0),1),"kW")
     +row("Rate",nf(d(N('ChargeRateMilePerHour'))),useImperial?"mi/h":"km/h")
     +row("Current",nf(N('ChargeAmps')),"A")
     +row("Voltage",nf(N('ChargerVoltage')),"V")
     +row("Added (AC)",nf(N('ACChargingEnergyIn'),1),"kWh")
     +row("Added (DC)",nf(N('DCChargingEnergyIn'),1),"kWh")
     +row("Time to full",nf(N('TimeToFullCharge'),1),"h")
     +row("Cable",S('ChargingCableType'))
     +row("Fast charger",S('FastChargerType'));
 }
 chInner+=row("Port door",B('ChargePortDoorOpen')==null?null:(B('ChargePortDoorOpen')?"open":"closed"))
   +row("Port latch",S('ChargePortLatch'));
 cards+=mcard("Charging",chInner);

 // Drive
 const speed=N('VehicleSpeed');const g=raw('Gear');const gear=(g==null||g==="<invalid>")?null:gearName(g);
 cards+=card("Drive",
   `<div class="big">${speed==null?'<span style="font-size:16px;color:var(--mut)">parked / idle</span>':fmt(speed,0)+'<span class="unit">mph</span>'}</div>`
   +((v.speed_history&&v.speed_history.length>1)?spark(v.speed_history,"var(--accent)"):"")
   +row("Gear",gear)
   +row("Heading",nf(N('GpsHeading')),"°")
   +row("Odometer",nf(d(N('Odometer'))),distUnit)
   +row("Destination",S('Destination'))
   +(N('MilesToArrival')!=null&&N('MilesToArrival')>0.1?row("ETA",`${nf(N('MilesToArrival'))} mi · ${Math.round(N('MinutesToArrival'))} min`):""));

 // Climate
 cards+=mcard("Climate",
   row("Inside",tc('InsideTemp'),tu)
   +row("Outside",tc('OutsideTemp'),tu)
   +row("A/C",onoff('HvacACEnabled'))
   +row("HVAC",S('HvacPower'))
   +row("Climate keeper",S('ClimateKeeperMode'))
   +row("Cabin overheat",S('CabinOverheatProtectionMode'))
   +row("Fan",(v=>{if(v==null)return null;if(typeof v==='number')return v===0?'Off':'Speed '+v;return String(v).replace(/([A-Za-z])(\d)/g,'$1 $2');})(S('HvacFanStatus'))));

 // Security
 cards+=mcard("Security",
   row("Locked",B('Locked')==null?null:(B('Locked')?"locked":"unlocked"))
   +row("Sentry",S('SentryMode')));

 // Doors & windows — door summary, then one labelled row per window
 const DOORLBL={DriverFront:"Driver front",DriverRear:"Driver rear",PassengerFront:"Passenger front",PassengerRear:"Passenger rear",TrunkFront:"Frunk",TrunkRear:"Trunk"};
 const dsraw=raw('DoorState');let doors=null;
 if(dsraw&&typeof dsraw==="object"){
   const open=Object.keys(dsraw).filter(k=>dsraw[k]).map(k=>DOORLBL[k]||k);
   doors=open.length?open.join(", "):"all closed";
 }
 const WINLBL={FdWindow:"Front left",FpWindow:"Front right",RdWindow:"Rear left",RpWindow:"Rear right"};
 const humanize=s=>typeof s==="string"?s.replace(/([a-z])([A-Z])/g,"$1 $2"):s;
 let winRows="";
 for(const k in WINLBL){const s=S(k);if(s!=null)winRows+=row(WINLBL[k],humanize(s));}
 cards+=mcard("Doors & windows",row("Doors",doors)+winRows);

 // Tire pressure (bar -> psi)
 const tp=k=>{const n=N(k);return n==null?null:(useImperial?Math.round(n*14.5038):Math.round(n*100)/100);};
 cards+=mcard("Tire pressure",
   row("Front L",tp('TpmsPressureFl'),pressUnit)+row("Front R",tp('TpmsPressureFr'),pressUnit)
   +row("Rear L",tp('TpmsPressureRl'),pressUnit)+row("Rear R",tp('TpmsPressureRr'),pressUnit));

 // Vehicle
 cards+=card("Vehicle",
   row("Name",typeof raw('VehicleName')==="string"?raw('VehicleName'):null)
   +row("VIN",v.vin)
   +row("Software",typeof raw('Version')==="string"?raw('Version'):null)
   +row("Network",typeof raw('NetworkInterface')==="string"?raw('NetworkInterface'):null)
   +row("Status",v.online?"online":"offline")
   +row("Last record",ago(v.last_seen_epoch))
   +row("Client",v.client_version||null)
   +row("Signals",Object.keys(f).length));

 // Other — only genuinely ungrouped signals (future-proofing)
 const grouped=new Set(["Soc","BatteryLevel","RatedRange","IdealBatteryRange","EstBatteryRange","EnergyRemaining","ChargeLimitSoc","ChargeState","DetailedChargeState","ACChargingPower","DCChargingPower","ChargeRateMilePerHour","ChargeAmps","ChargerVoltage","ChargerPhases","ACChargingEnergyIn","DCChargingEnergyIn","TimeToFullCharge","ChargingCableType","FastChargerType","FastChargerPresent","ChargePortDoorOpen","ChargePortLatch","VehicleSpeed","Gear","GpsHeading","Odometer","InsideTemp","OutsideTemp","HvacACEnabled","HvacPower","HvacFanStatus","ClimateKeeperMode","CabinOverheatProtectionMode","DoorState","Locked","SentryMode","FdWindow","FpWindow","RdWindow","RpWindow","TpmsPressureFl","TpmsPressureFr","TpmsPressureRl","TpmsPressureRr","VehicleName","Version","NetworkInterface","Location","LocatedAtHome","LocatedAtWork","LocatedAtFavorite","SettingTemperatureUnit","SettingDistanceUnit","ConnectionID","Status"]);
 const extra=Object.keys(f).filter(k=>!grouped.has(k));
 if(extra.length){cards+=card("Other signals",extra.map(k=>row(k,typeof raw(k)==="object"?JSON.stringify(raw(k)):(pretty(k,raw(k))!=null?pretty(k,raw(k)):raw(k)))).join(""));}

 return cards;
}

async function tick(){
 let st;try{st=await (await fetch(new URL('api/state',location.href),{cache:'no-store'})).json();}
 catch(e){$("#statusTxt").textContent="dashboard offline";return;}
 const fresh=st.last_record_epoch&&(st.now-st.last_record_epoch<600);
 $("#statusDot").style.background=fresh?"var(--good)":"var(--bad)";
 $("#statusTxt").textContent=fresh?"streaming":"no recent data";
 $("#ratePill").textContent=fmt(st.records_per_min,1)+" rec/min";
 $("#totalPill").textContent=fmt(st.total_records)+" records";
 $("#updatedPill").textContent="last record "+ago(st.last_record_epoch);
 $("#verPill").textContent="v"+(st.version||"—");
 const c=st.cert||{};
 $("#foot").innerHTML=`add-on v${esc(st.version||"—")} · uptime ${dur(st.uptime_seconds)} · namespace <b>${esc(st.namespace)}</b>`
   +(c.days_left!=null?` · TLS cert ${c.days_left>0?"valid "+fmt(c.days_left)+"d":"EXPIRED"} (${esc(c.not_after)})`:"");
 const vehiclesEl=$("#vehicles");
 $("#empty").style.display=st.vehicles.length?"none":"";
 const seen=new Set();
 for(const v of st.vehicles){
   const id="veh-"+v.vin;seen.add(id);
   let el=document.getElementById(id);
   if(!el){
     // Create the per-vehicle shell ONCE. The grid is re-rendered cheaply each tick
     // (text/SVG, no flash); the map iframe lives in its own card so it is never
     // recreated — we only change its src when the vehicle moves.
     el=document.createElement("div");el.id=id;
     el.innerHTML=`<h2 style="font-size:15px;margin:18px 0 10px">🚗 ${esc(v.display_name||v.vin)}</h2>`
       +`<div class="grid gridslot"></div>`
       +`<div class="card mapcard" style="display:none;margin-top:14px"><h3>Location</h3>`
       +`<div class="sub maploc"></div>`
       +`<iframe class="mapframe" loading="lazy"></iframe></div>`;
     vehiclesEl.appendChild(el);
   }
   el.querySelector(".gridslot").innerHTML=buildCards(v);
   // Pulse rows whose value changed since the last tick
   {const pk=prevKV[v.vin]||{};const nk={};
    el.querySelectorAll('.gridslot .kv').forEach(div=>{
      const sp=div.children;if(sp.length<2)return;
      const lbl=sp[0].textContent,val=sp[1].textContent;
      nk[lbl]=val;if(pk[lbl]!=null&&pk[lbl]!==val)div.classList.add('kv-pulse');
    });prevKV[v.vin]=nk;}
   const mapcard=el.querySelector(".mapcard"),frame=el.querySelector(".mapframe");
   if(v.location&&v.location.lat!=null){
     const la=v.location.lat,lo=v.location.lon,key=la.toFixed(5)+","+lo.toFixed(5);
     const ff=v.fields||{},gg=k=>ff[k]?ff[k].value:null;
     const geo=gg('LocatedAtHome')?"🏠 Home":gg('LocatedAtWork')?"🏢 Work":gg('LocatedAtFavorite')?"⭐ Favorite":"";
     const net=typeof gg('NetworkInterface')==="string"?gg('NetworkInterface'):"";
     el.querySelector(".maploc").innerHTML=`${la.toFixed(5)}, ${lo.toFixed(5)}`+(geo?` · ${esc(geo)}`:"")
       +(net?` · <span class="muted">${esc(net)}</span>`:"")
       +` · <a href="https://www.openstreetmap.org/?mlat=${la}&mlon=${lo}#map=15/${la}/${lo}" target="_blank">open map</a>`;
     if(mapKeys[v.vin]!==key){
       const d=0.01,bbox=[lo-d,la-d,lo+d,la+d].join("%2C");
       frame.src=`https://www.openstreetmap.org/export/embed.html?bbox=${bbox}&layer=mapnik&marker=${la}%2C${lo}`;
       mapKeys[v.vin]=key;
     }
     mapcard.style.display="";
   }else{mapcard.style.display="none";}
 }
 // Drop any vehicle shells that are no longer present.
 Array.from(vehiclesEl.children).forEach(ch=>{if(ch.id&&!seen.has(ch.id))ch.remove();});
}
tick();setInterval(tick,5000);
</script>
</body></html>"""

PAGE_SETUP = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fleet Telemetry Setup</title>
<style>
:root{--bg:#0b0f17;--card:#141b29;--card2:#1b2435;--line:#26314a;--txt:#e7edf7;--mut:#8a98b3;--accent:#3ea6ff;--good:#3ddc97;--warn:#ffb454;--bad:#ff5d5d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:780px;margin:0 auto;padding:24px 18px}
header{display:flex;align-items:center;gap:12px;margin-bottom:28px}
h1{font-size:18px;margin:0;font-weight:650;flex:1}
a.back{color:var(--mut);text-decoration:none;font-size:13px}a.back:hover{color:var(--txt)}
.progress{height:4px;background:var(--card2);border-radius:2px;margin-bottom:10px;overflow:hidden}
.progress-bar{height:100%;background:var(--accent);border-radius:2px;transition:width .4s}
.step-label{color:var(--mut);font-size:12px;margin-bottom:22px}
h2{font-size:20px;font-weight:650;margin:0 0 10px}
.subtitle{color:var(--mut);margin:0 0 22px;font-size:14px;line-height:1.6}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px;margin-bottom:14px}
.card h3{font-size:11.5px;font-weight:600;color:var(--mut);letter-spacing:.07em;text-transform:uppercase;margin:0 0 12px}
pre{background:var(--card2);border:1px solid var(--line);border-radius:8px;padding:14px;overflow-x:auto;font-size:12.5px;line-height:1.5;margin:0;white-space:pre-wrap;word-break:break-all}
.codewrap{position:relative;margin-bottom:4px}
.copy-btn{position:absolute;top:8px;right:8px;background:var(--card);border:1px solid var(--line);color:var(--mut);border-radius:6px;padding:3px 10px;font-size:11px;cursor:pointer}
.copy-btn:hover{color:var(--txt)}
.input-row{display:flex;gap:8px;margin-bottom:10px}
input[type=text]{flex:1;background:var(--card2);border:1px solid var(--line);border-radius:8px;padding:9px 13px;color:var(--txt);font-size:13.5px;outline:none}
input[type=text]:focus{border-color:var(--accent)}
.btn{padding:10px 20px;border-radius:8px;border:none;font-size:13.5px;font-weight:600;cursor:pointer}
.btn-primary{background:var(--accent);color:#04121f}.btn-primary:disabled{opacity:.45;cursor:not-allowed}
.btn-secondary{background:var(--card2);color:var(--txt);border:1px solid var(--line)}
.btn-outline{background:transparent;color:var(--accent);border:1px solid var(--accent)}
.result{padding:10px 14px;border-radius:8px;margin-top:10px;font-size:13px}
.result.ok{background:#0e2a1e;border:1px solid var(--good);color:var(--good)}
.result.err{background:#2a0e0e;border:1px solid var(--bad);color:var(--bad)}
.result.info{background:var(--card2);border:1px solid var(--line);color:var(--mut)}
.big-btns{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:8px}
.big-btn{background:var(--card);border:2px solid var(--line);border-radius:12px;padding:20px;text-align:left;cursor:pointer;transition:border-color .2s}
.big-btn:hover,.big-btn.sel{border-color:var(--accent)}
.big-btn h3{margin:0 0 6px;font-size:14px;font-weight:650;color:var(--txt)}
.big-btn p{margin:0;font-size:12.5px;color:var(--mut)}
.nav{display:flex;gap:10px;margin-top:28px;padding-top:20px;border-top:1px solid var(--line);align-items:center}
.nav .skip{margin-left:auto;color:var(--mut);background:none;border:none;font-size:12.5px;cursor:pointer;text-decoration:underline}
.mark-done{display:flex;align-items:center;gap:8px;padding:12px 14px;background:var(--card2);border-radius:8px;cursor:pointer;user-select:none;margin-top:4px;border:1px solid var(--line)}
.mark-done input{width:15px;height:15px;cursor:pointer;accent-color:var(--accent)}
.mark-done span{font-size:13.5px}
.check-row{display:flex;align-items:flex-start;gap:12px;padding:9px 0;border-bottom:1px solid var(--line)}
.check-row:last-child{border-bottom:0}
.check-num{width:22px;height:22px;border-radius:50%;background:var(--card2);border:1px solid var(--line);display:flex;align-items:center;justify-content:center;font-size:11px;flex-shrink:0;margin-top:1px}
.integration-opts{display:flex;flex-direction:column;gap:10px}
.int-opt{background:var(--card2);border:2px solid var(--line);border-radius:10px;padding:14px;cursor:pointer}
.int-opt:hover,.int-opt.sel{border-color:var(--accent)}
.int-opt h4{margin:0 0 4px;font-size:13.5px;font-weight:650;color:var(--txt)}
.int-opt p{margin:0;font-size:12px;color:var(--mut)}
.int-opt pre{margin-top:12px;font-size:12px}
.region-sel{display:flex;gap:8px;margin-bottom:14px}
.region-btn{padding:7px 16px;border-radius:7px;border:1px solid var(--line);background:var(--card2);color:var(--mut);cursor:pointer;font-size:12.5px}
.region-btn.sel{border-color:var(--accent);color:var(--accent);background:#0d1e36}
.status-items{display:flex;flex-direction:column;gap:8px}
.status-item{display:flex;align-items:center;gap:12px;padding:12px;background:var(--card2);border-radius:8px}
.si-icon{width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:14px;flex-shrink:0}
.si-ok{background:#0e2a1e}.si-wait{background:#1b2435}.si-bad{background:#2a0e0e}
.si-info{flex:1}.si-title{font-size:13px;font-weight:600}.si-sub{font-size:12px;color:var(--mut);margin-top:2px}
.summary-row{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--line)}
.summary-row:last-child{border-bottom:0}
.sum-icon{width:18px;height:18px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:10px;flex-shrink:0}
.sum-ok{background:#0e2a1e;color:var(--good)}.sum-pending{background:var(--card2);color:var(--mut)}
a{color:var(--accent)}
ol{padding-left:20px;margin:0}ol li{line-height:1.9;font-size:13.5px}
</style></head>
<body><div class="wrap">
<header>
  <h1>⚡ Fleet Telemetry Setup</h1>
  <a class="back" href="./">← Dashboard</a>
</header>
<div class="progress"><div class="progress-bar" id="pbar"></div></div>
<div class="step-label" id="stepLabel"></div>
<div id="stepContent"></div>
<div class="nav" id="nav"></div>
</div>
<script>
const esc=s=>String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function ago(e){if(!e)return"never";const s=Math.max(0,Date.now()/1000-e);
 if(s<60)return Math.round(s)+"s ago";if(s<3600)return Math.round(s/60)+"m ago";return Math.round(s/3600)+"h ago";}

let W={user_type:null,completed:false,current_step:1,steps:{},inputs:{}};
let pollTimer=null;

async function api(method,path,body){
  const opts={method,headers:{"Content-Type":"application/json"},cache:"no-store"};
  if(body)opts.body=JSON.stringify(body);
  const r=await fetch(new URL(path,location.href),opts);
  return r.json();
}

async function save(patch){
  // Locally merge patch into W
  for(const [k,v] of Object.entries(patch)){
    if(v&&typeof v==="object"&&!Array.isArray(v)&&W[k]&&typeof W[k]==="object")
      W[k]={...W[k],...v};
    else W[k]=v;
  }
  await api("POST","api/wizard/save",patch);
}

function visibleSteps(){
  const ut=W.user_type;
  if(!ut)return[1];
  if(ut==="new")return[1,2,3,4,5,6,7,8,9,11,12];
  if(ut==="teslamate_working")return[1,6,7,8,9,10,11,12];
  return[1,2,3,4,5,6,7,8,9,10,11,12];
}

function codebox(code,id){
  return `<div class="codewrap"><pre id="${esc(id)}">${esc(code)}</pre><button class="copy-btn" onclick="copyCode('${esc(id)}')">Copy</button></div>`;
}

function markDone(n){
  const done=W.steps[String(n)]==="done";
  return `<label class="mark-done"><input type="checkbox" ${done?"checked":""} onchange="toggleDone(${n},this.checked)"><span>I've completed this step</span></label>`;
}

function checkResultHtml(r){
  if(!r)return"";
  if(r.ok)return`<div class="result ok">✓ ${r.subject?esc(r.subject)+(r.days_left!=null?" · "+r.days_left+" days left":""):r.url?esc(r.url):"Check passed"}</div>`;
  return`<div class="result err">✗ ${esc(r.error||"Check failed")}</div>`;
}

function configResultHtml(r){
  if(!r)return"";
  if(r.ok){
    const vins=(r.vins||[]).map(v=>"…"+v.slice(-6)).join(", ");
    return`<div class="result ok">✓ Sent to ${(r.vins||[]).length} vehicle(s)${vins?" ("+vins+")":""}</div>`;
  }
  return`<div class="result err">✗ ${esc(r.error||"Send failed")}</div>`;
}

// ---- Step renderers ----

function renderStep1(){
  const ut=W.user_type;
  const tmSel=ut==="teslamate_broken"||ut==="teslamate_working";
  return`
<h2>Welcome to Fleet Telemetry</h2>
<p class="subtitle">Let's get you set up. Which best describes your situation?</p>
<div class="big-btns">
  <div class="big-btn${ut==="new"?" sel":""}" onclick="selectType('new')">
    <h3>I'm new to Fleet Telemetry</h3>
    <p>Set up Tesla streaming telemetry from scratch</p>
  </div>
  <div class="big-btn${tmSel?" sel":""}" onclick="showTMFollow()">
    <h3>I'm migrating TeslaMate to Fleet Telemetry</h3>
    <p>TeslaMate is already running — add streaming telemetry as its data source</p>
  </div>
</div>
<div id="tmfollow" style="display:${tmSel?"block":"none"};margin-top:16px">
  <div class="card"><h3>Is TeslaMate currently working?</h3>
  <div style="display:flex;gap:10px;margin-top:4px">
    <button class="btn ${ut==="teslamate_working"?"btn-primary":"btn-secondary"}" onclick="selectType('teslamate_working')">Yes — it's working</button>
    <button class="btn ${ut==="teslamate_broken"?"btn-primary":"btn-secondary"}" onclick="selectType('teslamate_broken')">No — it stopped working</button>
  </div></div>
</div>`;
}

function renderStep2(){
  return`
<h2>Prerequisites</h2>
<p class="subtitle">Before we start, make sure you have the following ready. This typically takes about 30 minutes.</p>
<div class="card">
  <h3>What you'll need</h3>
  <div class="check-row"><div class="check-num">1</div><div><b>Tesla Developer account</b> — sign up at <a href="https://developer.tesla.com" target="_blank">developer.tesla.com</a></div></div>
  <div class="check-row"><div class="check-num">2</div><div><b>A public-facing domain</b> pointing to your home IP — e.g. <code>telemetry.example.org</code></div></div>
  <div class="check-row"><div class="check-num">3</div><div><b>NGINX Proxy Manager</b> already running, with the ability to issue Let's Encrypt certificates</div></div>
  <div class="check-row"><div class="check-num">4</div><div><b>Port 443 forwarded</b> to your Home Assistant host in your router</div></div>
</div>`;
}

function renderStep3(){
  const domain=W.inputs.domain||"&lt;your-domain&gt;";
  return`
<h2>Tesla Developer App &amp; EC Key Pair</h2>
<p class="subtitle">Create a Tesla developer application and generate the cryptographic key pair it requires.</p>
<div class="card"><h3>1 — Create your Tesla app</h3>
  <ol>
    <li>Go to <a href="https://developer.tesla.com/en_US/dashboard" target="_blank">developer.tesla.com</a> and sign in</li>
    <li>Click <b>Create App</b> and fill in the details</li>
    <li>Under scopes, request <b>vehicle_device_data</b></li>
    <li>Note your <b>Client ID</b> — you'll need it in the next step</li>
  </ol>
</div>
<div class="card"><h3>2 — Generate an EC key pair</h3>
  <p style="color:var(--mut);font-size:12.5px;margin:0 0 10px">Run on any machine with openssl. Keep <code>private.pem</code> secure.</p>
  ${codebox("openssl ecparam -name prime256v1 -genkey -noout -out private.pem\nopenssl ec -in private.pem -pubout -out public.pem","kp-cmd")}
</div>
<div class="card"><h3>3 — Host the public key</h3>
  <p style="color:var(--mut);font-size:12.5px;margin:0 0 10px">Upload <code>public.pem</code> to your web server so it's reachable at exactly:</p>
  <pre>https://${esc(domain)}/.well-known/appspecific/com.tesla.3p.public-key.pem</pre>
</div>
${markDone(3)}`;
}

function renderStep4(){
  const domain=W.inputs.domain||"";
  const res=W.inputs.pubkey_check||null;
  return`
<h2>Verify Public Key URL</h2>
<p class="subtitle">Enter your telemetry domain. We'll fetch the public key URL from inside the container to confirm Tesla can reach it.</p>
<div class="card"><h3>Your telemetry domain</h3>
  <div class="input-row">
    <input type="text" id="domainInput" placeholder="telemetry.example.org" value="${esc(domain)}">
    <button class="btn btn-outline" onclick="checkPubkey()">Test URL</button>
  </div>
  <p style="color:var(--mut);font-size:12px;margin:0">Checks <code>https://&lt;domain&gt;/.well-known/appspecific/com.tesla.3p.public-key.pem</code> for a valid EC public key.</p>
  <div id="pubkeyResult">${checkResultHtml(res)}</div>
</div>`;
}

function renderStep5(){
  const domain=W.inputs.domain||"&lt;your-domain&gt;";
  const clientId=W.inputs.client_id||"&lt;your-client-id&gt;";
  const curl=`# 1. Get a Partner Auth token
curl -X POST https://auth.tesla.com/oauth2/v3/token \\
  -H "Content-Type: application/x-www-form-urlencoded" \\
  -d "grant_type=client_credentials&client_id=${clientId}&client_secret=<YOUR_SECRET>&scope=openid+vehicle_device_data+offline_access"

# 2. Register your domain (use access_token from step 1)
curl -X POST https://fleet-api.prd.na.vn.cloud.tesla.com/api/1/partner_accounts \\
  -H "Authorization: Bearer <ACCESS_TOKEN>" \\
  -H "Content-Type: application/json" \\
  -d '{"domain":"${domain}"}'`;
  return`
<h2>Register Partner Account</h2>
<p class="subtitle">Tell Tesla's API about your app domain. One-time registration.</p>
<div class="card"><h3>Your Tesla client ID</h3>
  <div class="input-row" style="margin-bottom:4px">
    <input type="text" id="clientIdInput" placeholder="abc123def456..." value="${esc(W.inputs.client_id||"")}">
  </div>
  <p style="color:var(--mut);font-size:12px;margin:0 0 14px">Pre-fills the commands below.</p>
  <h3 style="margin-top:0">Registration commands</h3>
  ${codebox(curl,"reg-cmd")}
</div>
${markDone(5)}`;
}

function renderStep6(){
  const domain=W.inputs.domain||"&lt;your-domain&gt;";
  return`
<h2>Issue TLS Certificate in NPM</h2>
<p class="subtitle">The add-on fetches its certificate from NGINX Proxy Manager automatically. Issue a Let's Encrypt cert for your telemetry domain first.</p>
<div class="card"><h3>Steps in NGINX Proxy Manager</h3>
  <ol>
    <li>Open NPM and go to <b>SSL Certificates</b></li>
    <li>Click <b>Add SSL Certificate → Let's Encrypt</b></li>
    <li>Domain: <code>${domain}</code></li>
    <li>Complete DNS or HTTP challenge and click <b>Save</b></li>
  </ol>
</div>
<div class="card" style="border-color:var(--warn)">
  <h3 style="color:var(--warn)">Important</h3>
  <p style="margin:0;font-size:13px">The <b>npm_cert_domain</b> you set in the add-on must match the domain on this certificate exactly (case-sensitive).</p>
</div>
${markDone(6)}`;
}

function renderStep7(){
  return`
<h2>Create NPM Stream (TCP Passthrough)</h2>
<p class="subtitle">Fleet Telemetry uses mTLS — the TLS handshake must reach the add-on directly. Use a <b>Stream</b>, not a Proxy Host, so TLS is not terminated at the proxy.</p>
<div class="card"><h3>Traffic flow</h3>
<pre>Tesla Vehicle
  ↓  TLS — NOT terminated at proxy
[443] → NPM Stream (TCP passthrough)
  ↓
[4443] → Fleet Telemetry add-on
  ↓  mTLS handshake + telemetry data</pre></div>
<div class="card"><h3>Steps in NGINX Proxy Manager</h3>
  <ol>
    <li>Go to <b>Streams</b> in NPM (not Proxy Hosts)</li>
    <li>Click <b>Add Stream</b></li>
    <li>Incoming port: <b>443</b></li>
    <li>Forward host: your Home Assistant IP address</li>
    <li>Forward port: <b>4443</b> (the add-on's telemetry port)</li>
    <li>Protocol: <b>TCP</b> only — disable UDP</li>
    <li><b>Do NOT enable SSL termination</b> — leave it off</li>
    <li>Save</li>
  </ol>
</div>
${markDone(7)}`;
}

function renderStep8(){
  const domain=W.inputs.domain||"";
  const res=W.inputs.cert_check||null;
  return`
<h2>Configure the Add-on</h2>
<p class="subtitle">Fill in these fields in the add-on's <b>Configuration</b> tab in Home Assistant, then restart the add-on.</p>
<div class="card"><h3>Required fields</h3>
  <table style="width:100%;border-collapse:collapse;font-size:13px">
    <tr><td style="padding:6px 0;color:var(--mut);width:170px;vertical-align:top">npm_url</td><td>Base URL of your NPM admin panel, e.g. <code>https://proxy.example.org:81</code></td></tr>
    <tr><td style="padding:6px 0;color:var(--mut);vertical-align:top">npm_email</td><td>NPM admin email address</td></tr>
    <tr><td style="padding:6px 0;color:var(--mut);vertical-align:top">npm_password</td><td>NPM admin password</td></tr>
    <tr><td style="padding:6px 0;color:var(--mut);vertical-align:top">npm_cert_domain</td><td><code>${esc(domain||"your telemetry domain from the previous step")}</code></td></tr>
  </table>
</div>
<div class="card"><h3>Verify certificate after restart</h3>
  <p style="color:var(--mut);font-size:13px;margin:0 0 12px">Save your configuration, restart the add-on, then click <b>Verify Certificate</b> to confirm it loaded correctly. <b>Next</b> is unlocked only when the certificate check passes.</p>
  <button class="btn btn-outline" onclick="checkCert()">Verify Certificate</button>
  <div id="certResult">${checkResultHtml(res)}</div>
</div>`;
}

function renderStep9(){
  const domain=W.inputs.domain||"&lt;your-domain&gt;";
  const region=W.inputs.region||"na";
  const hosts={na:"https://fleet-api.prd.na.vn.cloud.tesla.com",eu:"https://fleet-api.prd.eu.vn.cloud.tesla.com",cn:"https://fleet-api.prd.cn.vn.cloud.tesla.com"};
  const host=hosts[region];
  const cfg=`{
  "hostname": "${domain}",
  "port": 443,
  "ca": "<Let's Encrypt R3+R10 chain — see note below>",
  "fields": {
    "VehicleSpeed":              {"interval_seconds": 10},
    "Location":                  {"interval_seconds": 30},
    "GpsHeading":                {"interval_seconds": 10},
    "Soc":                       {"interval_seconds": 30},
    "BatteryLevel":              {"interval_seconds": 30},
    "Gear":                      {"interval_seconds": 5},
    "PackVoltage":               {"interval_seconds": 10},
    "PackCurrent":               {"interval_seconds": 10},
    "RatedRange":                {"interval_seconds": 60},
    "EstBatteryRange":           {"interval_seconds": 60},
    "IdealBatteryRange":         {"interval_seconds": 60},
    "DetailedChargeState":       {"interval_seconds": 30},
    "ACChargingPower":           {"interval_seconds": 30},
    "DCChargingPower":           {"interval_seconds": 30},
    "ACChargingEnergyIn":        {"interval_seconds": 30},
    "DCChargingEnergyIn":        {"interval_seconds": 30},
    "ChargeAmps":                {"interval_seconds": 30},
    "ChargerVoltage":            {"interval_seconds": 30},
    "ChargerPhases":             {"interval_seconds": 60},
    "ChargeLimitSoc":            {"interval_seconds": 60},
    "TimeToFullCharge":          {"interval_seconds": 60},
    "ChargingCableType":         {"interval_seconds": 60},
    "FastChargerPresent":        {"interval_seconds": 60},
    "FastChargerType":           {"interval_seconds": 60},
    "ChargeCurrentRequest":      {"interval_seconds": 30},
    "ChargeCurrentRequestMax":   {"interval_seconds": 60},
    "ChargePortDoorOpen":        {"interval_seconds": 30},
    "BatteryHeaterOn":           {"interval_seconds": 30},
    "NotEnoughPowerToHeat":      {"interval_seconds": 30},
    "InsideTemp":                {"interval_seconds": 60},
    "OutsideTemp":               {"interval_seconds": 60},
    "HvacACEnabled":             {"interval_seconds": 60},
    "HvacPower":                 {"interval_seconds": 60},
    "HvacFanStatus":             {"interval_seconds": 60},
    "HvacLeftTemperatureRequest":  {"interval_seconds": 60},
    "HvacRightTemperatureRequest": {"interval_seconds": 60},
    "ClimateKeeperMode":         {"interval_seconds": 60},
    "PreconditioningEnabled":    {"interval_seconds": 30},
    "DefrostMode":               {"interval_seconds": 30},
    "RearDefrostEnabled":        {"interval_seconds": 30},
    "Odometer":                  {"interval_seconds": 60},
    "Version":                   {"interval_seconds": 3600},
    "Locked":                    {"interval_seconds": 60},
    "SentryMode":                {"interval_seconds": 60},
    "DoorState":                 {"interval_seconds": 60},
    "FdWindow":                  {"interval_seconds": 60},
    "FpWindow":                  {"interval_seconds": 60},
    "RdWindow":                  {"interval_seconds": 60},
    "RpWindow":                  {"interval_seconds": 60},
    "TpmsPressureFl":            {"interval_seconds": 300},
    "TpmsPressureFr":            {"interval_seconds": 300},
    "TpmsPressureRl":            {"interval_seconds": 300},
    "TpmsPressureRr":            {"interval_seconds": 300},
    "DestinationName":                       {"interval_seconds": 30},
    "DestinationLocation":                   {"interval_seconds": 30},
    "MilesToArrival":                        {"interval_seconds": 30},
    "MinutesToArrival":                      {"interval_seconds": 30},
    "RouteLastUpdated":                      {"interval_seconds": 30},
    "RouteTrafficMinutesDelay":              {"interval_seconds": 30},
    "ExpectedEnergyPercentAtTripArrival":    {"interval_seconds": 30}
  }
}`;
  const domainVal=W.inputs.domain||"";
  const res=W.inputs.telemetry_config_result||null;
  return`
<h2>Configure Vehicle Telemetry</h2>
<p class="subtitle">Tell your Tesla vehicle where to stream data. This sends the configuration directly from the add-on using your stored credentials.</p>
<div class="card"><h3>Telemetry domain</h3>
  <p style="color:var(--mut);font-size:12.5px;margin:0 0 8px">The public hostname your vehicle will connect to — must match your TLS certificate.</p>
  <div class="input-row">
    <input type="text" id="telDomainInput" placeholder="telemetry.example.org" value="${esc(domainVal)}">
  </div>
</div>
<div class="region-sel">
  <button class="region-btn${region==="na"?" sel":""}" onclick="setRegion('na')">North America</button>
  <button class="region-btn${region==="eu"?" sel":""}" onclick="setRegion('eu')">Europe</button>
  <button class="region-btn${region==="cn"?" sel":""}" onclick="setRegion('cn')">China</button>
</div>
<div class="card"><h3>What will be sent</h3>
  <p style="color:var(--mut);font-size:12.5px;margin:0 0 10px">57 telemetry fields · port 443 · CA chain from your TLS cert. VINs and credentials are read automatically from the add-on.</p>
  ${codebox(cfg,"cfg-json")}
</div>
<div class="card"><h3>Send configuration</h3>
  <p style="color:var(--mut);font-size:13px;margin:0 0 12px">Requires <code>teslamate_shim_client_id</code> and <code>teslamate_shim_refresh_token</code> to be set, and the app private key at <code>/share/tesla-fleet/private-key.pem</code>.</p>
  <button class="btn btn-outline" onclick="sendTelemetryConfig()">Send to Vehicle</button>
  <div id="telCfgResult">${configResultHtml(res)}</div>
</div>`;
}

function renderStep10(){
  const sel=W.inputs.tm_path||null;
  const haHost=location.hostname;
  const paths=[
    {id:"bridge",title:"Streaming bridge (recommended)",desc:"Add-on runs a bundled websocket server on port 8081. TeslaMate receives live streaming data — best data freshness.",
     config:`# In add-on Configuration tab:\nenable_teslamate_bridge: true\n\n# In TeslaMate environment:\nTESLA_WSS_HOST=wss://<your-domain>:8081\nTESLA_WSS_USE_VIN=true`},
    {id:"shim",title:"Fleet-API shim (polling)",desc:"TeslaMate polls this add-on's built-in Fleet API shim. No real Tesla API calls — data assembled from streaming telemetry.",
     config:`# In TeslaMate environment:\nTESLA_API_HOST=http://${haHost}:8085\n\n# In TeslaMate UI, per car:\nuse_streaming_api = false`},
    {id:"shim_auth",title:"Auth-free shim",desc:"Same as Fleet-API shim, but also mocks Tesla auth so TeslaMate never needs real Tesla credentials.",
     config:`# In TeslaMate environment:\nTESLA_API_HOST=http://${haHost}:8085\nTESLA_AUTH_HOST=http://${haHost}:8085\n\n# In TeslaMate UI, per car:\nuse_streaming_api = false`},
  ];
  return`
<h2>TeslaMate Integration</h2>
<p class="subtitle">Choose how TeslaMate will receive vehicle data from this add-on.</p>
<div class="integration-opts">
${paths.map(p=>`<div class="int-opt${sel===p.id?" sel":""}" onclick="selectTMPath('${p.id}')">
  <h4>${esc(p.title)}</h4><p>${esc(p.desc)}</p>
  ${sel===p.id?codebox(p.config,"tm-cfg-"+p.id):""}
</div>`).join("")}
</div>
${markDone(10)}`;
}

function renderStep11(){
  const st=W.inputs.verify_state||{};
  function si(icon,cls,title,sub){
    return`<div class="status-item"><div class="si-icon ${cls}">${icon}</div><div class="si-info"><div class="si-title">${title}</div><div class="si-sub">${sub}</div></div></div>`;
  }
  const certOk=st.cert_ok,recOk=st.records_ok,vins=st.vins||[];
  return`
<h2>Verification</h2>
<p class="subtitle">Confirming everything is connected. The vehicle streams every few minutes when awake — you may need to wait or manually wake the car from the Tesla app.</p>
<div class="card"><div class="status-items">
  ${si(certOk?"✓":"…",certOk?"si-ok":"si-wait","TLS Certificate",certOk?`Valid · ${esc(st.cert_subject||"")} · ${st.cert_expiry} days remaining`:"Checking…")}
  ${si(recOk?"✓":"…",recOk?"si-ok":"si-wait","Telemetry Records",recOk?`${esc(String(st.total_records||0))} records received · last seen ${esc(ago(st.last_epoch||0))} · VIN …${esc((vins[0]||"").slice(-6))}`:"Waiting for first record from your vehicle…")}
</div></div>
<p style="color:var(--mut);font-size:12.5px;text-align:center;margin-top:14px">${recOk?"Records are flowing — you're all set!":"Polling every 5 seconds…"}</p>`;
}

function renderStep12(){
  const ut=W.user_type;
  const items=[
    {label:"Tesla developer app created",ok:W.steps["3"]==="done"||!visibleSteps().includes(3)},
    {label:"EC key pair generated &amp; public key hosted",ok:!!(W.inputs.pubkey_check?.ok)||!visibleSteps().includes(4)},
    {label:"Partner account registered",ok:W.steps["5"]==="done"||!visibleSteps().includes(5)},
    {label:"NPM certificate issued",ok:W.steps["6"]==="done"},
    {label:"NPM Stream configured",ok:W.steps["7"]==="done"},
    {label:"Add-on configured &amp; certificate verified",ok:!!(W.inputs.cert_check?.ok)},
    {label:"Vehicle telemetry configured",ok:W.steps["9"]==="done"},
    ...(ut!=="new"?[{label:"TeslaMate integration configured",ok:W.steps["10"]==="done"}]:[]),
    {label:"Telemetry records verified",ok:!!(W.inputs.verify_state?.records_ok)},
  ];
  return`
<h2>Setup Complete 🎉</h2>
<p class="subtitle">Your Fleet Telemetry add-on is up and running. Here's a summary of what was configured.</p>
<div class="card"><h3>Setup summary</h3>
  ${items.map(i=>`<div class="summary-row"><div class="sum-icon ${i.ok?"sum-ok":"sum-pending"}">${i.ok?"✓":"○"}</div><div style="font-size:13px">${i.label}</div></div>`).join("")}
</div>
<div class="card" style="background:transparent;border-color:var(--accent)">
  <p style="margin:0;font-size:13.5px">The <b>Setup Guide</b> is always accessible from the dashboard header if you need to revisit any of these steps.</p>
</div>`;
}

// ---- Interactions ----

async function selectType(t){
  document.getElementById("tmfollow").style.display="block";
  await save({user_type:t,current_step:1});
  render();
}

function showTMFollow(){
  document.getElementById("tmfollow").style.display="block";
}

async function toggleDone(stepNum,checked){
  const steps={...W.steps};
  if(checked)steps[String(stepNum)]="done"; else delete steps[String(stepNum)];
  await save({steps});
  updateNav();
}

async function checkPubkey(){
  const inp=document.getElementById("domainInput");if(!inp)return;
  const raw=inp.value.trim();
  const domain=raw.replace(/^https?:\/\//,"").replace(/\/.*$/,"");
  if(!domain)return;
  document.getElementById("pubkeyResult").innerHTML=`<div class="result info">Checking ${esc(domain)}…</div>`;
  const r=await api("POST","api/wizard/check",{check:"pubkey",domain});
  await save({inputs:{...W.inputs,domain,pubkey_check:r}});
  document.getElementById("pubkeyResult").innerHTML=checkResultHtml(r);
  updateNav();
}

async function checkCert(){
  document.getElementById("certResult").innerHTML=`<div class="result info">Checking certificate…</div>`;
  const r=await api("POST","api/wizard/check",{check:"cert"});
  await save({inputs:{...W.inputs,cert_check:r}});
  document.getElementById("certResult").innerHTML=checkResultHtml(r);
  updateNav();
}

async function sendTelemetryConfig(){
  const el=document.getElementById("telCfgResult");
  const inp=document.getElementById("telDomainInput");
  const raw=inp?inp.value.trim():(W.inputs.domain||"");
  const domain=raw.replace(/^https?:\/\//,"").replace(/\/.*$/,"");
  if(!domain){
    if(el)el.innerHTML=`<div class="result err">✗ Enter your telemetry domain above before sending.</div>`;
    return;
  }
  if(el)el.innerHTML=`<div class="result info">Sending… this may take up to 30 seconds while the signing proxy starts.</div>`;
  const region=W.inputs.region||"na";
  const r=await api("POST","api/wizard/check",{check:"send_telemetry_config",domain,region});
  const newInputs={...W.inputs,domain,telemetry_config_result:r};
  const newSteps=r.ok?{...W.steps,"9":"done"}:W.steps;
  await save({inputs:newInputs,steps:newSteps});
  if(el)el.innerHTML=configResultHtml(r);
  updateNav();
}

async function setRegion(r){
  await save({inputs:{...W.inputs,region:r}});
  render();
}

async function selectTMPath(p){
  await save({inputs:{...W.inputs,tm_path:p}});
  render();
}

function copyCode(id){
  const el=document.getElementById(id);if(!el)return;
  navigator.clipboard.writeText(el.textContent).then(()=>{
    const btn=el.parentElement.querySelector(".copy-btn");
    if(btn){btn.textContent="Copied!";setTimeout(()=>btn.textContent="Copy",1500);}
  });
}

// ---- Navigation ----

function canAdvance(){
  const s=W.current_step;
  if(s===1)return!!W.user_type;
  if(s===4)return!!(W.inputs.pubkey_check&&W.inputs.pubkey_check.ok);
  if(s===8)return!!(W.inputs.cert_check&&W.inputs.cert_check.ok);
  if(s===10)return!!(W.inputs.tm_path&&W.steps["10"]==="done");
  const manual=[3,5,6,7,9];
  if(manual.includes(s))return W.steps[String(s)]==="done";
  return true;
}

async function goNext(){
  if(!canAdvance())return;
  // Save client_id from step 5 input before advancing
  if(W.current_step===5){
    const inp=document.getElementById("clientIdInput");
    if(inp&&inp.value.trim())await save({inputs:{...W.inputs,client_id:inp.value.trim()}});
  }
  const vs=visibleSteps();
  const idx=vs.indexOf(W.current_step);
  if(idx<vs.length-1){
    const next=vs[idx+1];
    const patch={current_step:next};
    if(next===12)patch.completed=true;
    await save(patch);
    render();window.scrollTo(0,0);
  }
}

async function goPrev(){
  const vs=visibleSteps();
  const idx=vs.indexOf(W.current_step);
  if(idx>0){await save({current_step:vs[idx-1]});render();window.scrollTo(0,0);}
}

async function skipVerify(){
  await save({current_step:12,completed:true});render();window.scrollTo(0,0);
}

// ---- Verify polling ----

function startVerifyPoll(){
  if(pollTimer)return;
  pollVerify();
  pollTimer=setInterval(pollVerify,5000);
}

function stopVerifyPoll(){if(pollTimer){clearInterval(pollTimer);pollTimer=null;}}

async function pollVerify(){
  try{
    const[cr,rr]=await Promise.all([
      api("POST","api/wizard/check",{check:"cert"}),
      api("POST","api/wizard/check",{check:"records"}),
    ]);
    const st={cert_ok:cr.ok,cert_subject:cr.subject||"",cert_expiry:cr.days_left,
              records_ok:rr.ok,total_records:rr.total||0,last_epoch:rr.last_epoch||0,vins:rr.vins||[]};
    // Merge without triggering full re-render (update display in-place)
    W.inputs={...W.inputs,verify_state:st};
    await api("POST","api/wizard/save",{inputs:{verify_state:st}});
    const sc=document.getElementById("stepContent");
    if(sc&&W.current_step===11)sc.innerHTML=renderStep11();
    if(rr.ok){stopVerifyPoll();updateNav();}
  }catch(e){/* ignore transient errors */}
}

// ---- Render ----

const STEP_TITLES={1:"User Type",2:"Prerequisites",3:"Tesla Developer App",4:"Verify Public Key",
  5:"Register Partner Account",6:"NPM Certificate",7:"NPM Stream",8:"Add-on Configuration",
  9:"Vehicle Telemetry Config",10:"TeslaMate Integration",11:"Verification",12:"Done"};
const RENDERERS={1:renderStep1,2:renderStep2,3:renderStep3,4:renderStep4,5:renderStep5,
  6:renderStep6,7:renderStep7,8:renderStep8,9:renderStep9,10:renderStep10,11:renderStep11,12:renderStep12};

function render(){
  const vs=visibleSteps();
  const idx=vs.indexOf(W.current_step);
  document.getElementById("pbar").style.width=((idx+1)/vs.length*100)+"%";
  document.getElementById("stepLabel").textContent=`Step ${idx+1} of ${vs.length} · ${STEP_TITLES[W.current_step]||""}`;
  const fn=RENDERERS[W.current_step];
  document.getElementById("stepContent").innerHTML=fn?fn():`<p style="color:var(--mut)">Unknown step.</p>`;
  updateNav();
  if(W.current_step===11&&!(W.inputs.verify_state?.records_ok))startVerifyPoll();
  else stopVerifyPoll();
}

function updateNav(){
  const vs=visibleSteps();
  const idx=vs.indexOf(W.current_step);
  const isFirst=idx===0,isLast=W.current_step===12,ok=canAdvance();
  let html="";
  if(!isFirst)html+=`<button class="btn btn-secondary" onclick="goPrev()">← Back</button>`;
  if(!isLast)html+=`<button class="btn btn-primary" onclick="goNext()"${ok?"":" disabled"}>Next →</button>`;
  else html+=`<a class="btn btn-primary" href="./" style="text-decoration:none">Go to Dashboard →</a>`;
  if(W.current_step===11&&!(W.inputs.verify_state?.records_ok))
    html+=`<button class="skip" onclick="skipVerify()">Skip for now</button>`;
  document.getElementById("nav").innerHTML=html;
}

// ---- Init ----
(async()=>{
  try{
    const data=await api("GET","api/wizard/state");
    if(data&&typeof data==="object")
      W=Object.assign({user_type:null,completed:false,current_step:1,steps:{},inputs:{}},data);
  }catch(e){}
  render();
})();
</script>
</body></html>"""


def main():
    threading.Thread(target=_tail_records, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[fleet-telemetry-web] dashboard listening on :{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
