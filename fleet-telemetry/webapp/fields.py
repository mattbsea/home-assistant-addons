"""Single source of truth for Fleet Telemetry field handling.

Consolidates logic that was duplicated across server.py / shim.py / bridge.py: the meta-key sets,
numeric coercion, enum stripping, gear normalization, location parsing, the small enum decoders
(fan / window / defrost), and the vehicle_data -> telemetry-field mapping. Each consumer imports
from here instead of carrying its own copy.
"""
import re

# Keys that are never vehicle telemetry fields. The dashboard and bridge keep the connectivity
# frame keys (ConnectionID / NetworkInterface / Status) because the dashboard renders them; the
# shim drops them so TeslaMate never sees them as vehicle data.
META_BASE = {"CreatedAt", "IsResend", "Vin"}
META_SHIM = META_BASE | {"ConnectionID", "NetworkInterface", "Status"}


def num(v):
    """Coerce to float, or None if not numeric."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_location(val):
    """Return (lat, lon) from a Location value in dict or 'lat,lon' string shape; else (None, None)."""
    if isinstance(val, dict):
        lat = val.get("latitude", val.get("Latitude"))
        lon = val.get("longitude", val.get("Longitude"))
        if lat is not None and lon is not None:
            return num(lat), num(lon)
    if isinstance(val, str) and "," in val:
        parts = val.split(",")
        if len(parts) == 2:
            return num(parts[0]), num(parts[1])
    return None, None


def strip_state(v):
    """Strip Tesla's verbose enum prefix: 'DetailedChargeStateDisconnected' -> 'Disconnected'.

    Non-strings pass through; the sentinel '<invalid>'/'invalid'/'' map to None.
    """
    if v is None or not isinstance(v, str):
        return v
    if v in ("<invalid>", "invalid", ""):
        return None
    i = v.rfind("State")
    if 0 <= i and i + 5 < len(v):
        return v[i + 5:]
    return v


def round_int(v):
    n = num(v)
    return int(round(n)) if n is not None else None


def as_bool(v):
    return v if isinstance(v, bool) else False


def fan_speed(v):
    """HvacFanStatus enum ('HvacFanStatusSpeed3') or bare int -> integer fan level."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v)
    if "Off" in s:
        return 0
    m = re.search(r"\d+", s)
    return int(m.group()) if m else None


def window_state(v):
    """Window enum ('WindowStateClosed'/'WindowStateVenting'/'WindowStateOpen') -> 0/1/2."""
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


def defrost_on(v):
    """DefrostMode enum -> any non-Off value means defrost is active."""
    if v is None:
        return None
    return "Off" not in str(v)


def gear_letter(v):
    """Map any gear representation (DriveGearP / ShiftStateP / P / Drive...) to P/R/N/D."""
    s = str(v).upper()
    if s and s[-1] in "PRND":
        return s[-1]
    if "PARK" in s:
        return "P"
    if "REV" in s:
        return "R"
    if "NEUT" in s:
        return "N"
    if "DRIVE" in s:
        return "D"
    return s


def prime_to_fields(p):
    """Map a Tesla vehicle_data response (the shim's 'prime') back to telemetry field names so the
    dashboard's existing cards can render it. Used only to fill gaps the live stream hasn't (or
    won't) provide."""
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
    put("ChargeRateMilePerHour", cs.get("charge_rate"))
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
    put("ChargePortLatch", cs.get("charge_port_latch"))
    if isinstance(cs.get("battery_heater_on"), bool):
        put("BatteryHeaterOn", cs["battery_heater_on"])
    if isinstance(cs.get("not_enough_power_to_heat"), bool):
        put("NotEnoughPowerToHeat", cs["not_enough_power_to_heat"])
    put("InsideTemp", cl.get("inside_temp"))
    put("OutsideTemp", cl.get("outside_temp"))
    put("ClimateKeeperMode", cl.get("climate_keeper_mode"))
    put("CabinOverheatProtectionMode", cl.get("cabin_overheat_protection"))
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
    put("VehicleName", vs.get("vehicle_name"))
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
