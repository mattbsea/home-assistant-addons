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


# Tesla "Type" enums use a verbose prefix that ends in "Type", not "State", so strip_state misses
# them (live telemetry sends ChargingCableType="CableTypeSAE" -> TeslaMate stored "CableTypeSAE").
_ENUM_TYPE_PREFIXES = ("CableType", "FastChargerType")


def strip_enum(v):
    """Normalize a Tesla enum string for TeslaMate: strip the verbose 'Type' prefix
    ('CableTypeSAE' -> 'SAE'), else fall back to strip_state ('...State' enums). Non-strings and the
    invalid/empty sentinels pass through strip_state's rules (-> None)."""
    if isinstance(v, str):
        for p in _ENUM_TYPE_PREFIXES:
            if v.startswith(p) and len(v) > len(p):
                return v[len(p):]
    return strip_state(v)


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
    """Window enum ('WindowStateClosed'/'WindowStateVenting'/'WindowStateOpen') -> 0/1/2. The live
    stream sends the enum string; the Fleet-API seed already gives the int — pass that through."""
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v)
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


def fleet_api_to_fields(p):
    """Map a Tesla Fleet-API ``vehicle_data`` response to flat telemetry field names, so the response
    can seed the single per-VIN field map at startup (and refresh the two non-streamed charge fields
    at charge start). Telemetry overwrites these as it streams (last-writer-wins)."""
    ds = p.get("drive_state") or {}; cs = p.get("charge_state") or {}
    cl = p.get("climate_state") or {}; vs = p.get("vehicle_state") or {}; vc = p.get("vehicle_config") or {}
    gs = p.get("gui_settings") or {}
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
    put("ChargerPilotCurrent", cs.get("charger_pilot_current"))   # Fleet-API only (not streamed)
    put("FastChargerBrand", cs.get("fast_charger_brand"))         # Fleet-API only (not streamed)
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
    # climate.battery_heater_no_power and charge.not_enough_power_to_heat are the same signal in the
    # Fleet API; the live stream sends only NotEnoughPowerToHeat. Use charge's value first (set above),
    # else fall back to the climate one so the seed populates it.
    if "NotEnoughPowerToHeat" not in out and isinstance(cl.get("battery_heater_no_power"), bool):
        put("NotEnoughPowerToHeat", cl["battery_heater_no_power"])
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
    put("DestinationName", ds.get("active_route_destination"))
    if ds.get("active_route_latitude") is not None and ds.get("active_route_longitude") is not None:
        put("DestinationLocation", {"latitude": ds["active_route_latitude"], "longitude": ds["active_route_longitude"]})
    put("MilesToArrival", ds.get("active_route_miles_to_arrival"))
    put("MinutesToArrival", ds.get("active_route_minutes_to_arrival"))
    put("RouteTrafficMinutesDelay", ds.get("active_route_traffic_minutes_delay"))
    put("ExpectedEnergyPercentAtTripArrival", ds.get("active_route_energy_at_arrival"))
    put("Odometer", vs.get("odometer"))
    put("Version", vs.get("car_version"))
    put("VehicleName", vs.get("vehicle_name"))
    sw = vs.get("software_update") or {}        # Fleet API exposes pending-update status here
    put("SoftwareUpdateVersion", sw.get("version"))
    put("SoftwareUpdateInstallationPercentComplete", sw.get("install_perc"))
    put("SoftwareUpdateDownloadPercentComplete", sw.get("download_perc"))
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
    put("VehicleConfig", p.get("vehicle_config"))   # whole config blob, served verbatim by the shim
    put("SettingTemperatureUnit", gs.get("gui_temperature_units"))
    put("SettingDistanceUnit", gs.get("gui_distance_units"))
    return out


# Telemetry fields requested from the vehicle (name -> stream interval). Single source
# of the roster used to build the fleet_telemetry_config sent to the car.
TELEMETRY_FIELDS = {
    "VehicleSpeed":              {"interval_seconds": 10},
    # Location/PackVoltage/PackCurrent are "drive-grade": interval_seconds=1 ALLOWS 1 Hz, and
    # minimum_delta gates transmission to actual changes — so they stream densely while driving (values
    # swing) and go near-silent when parked (stable), with resend_interval_seconds as a heartbeat. This
    # is what makes TeslaMate's power/regen-over-dt integral accurate without always-on cost.
    "Location":                  {"interval_seconds": 1, "minimum_delta": 3, "resend_interval_seconds": 60},
    "GpsHeading":                {"interval_seconds": 10},
    "Soc":                       {"interval_seconds": 30, "minimum_delta": 0.1, "resend_interval_seconds": 300},
    "BatteryLevel":              {"interval_seconds": 30, "minimum_delta": 1, "resend_interval_seconds": 300},
    "Gear":                      {"interval_seconds": 5},
    "PackVoltage":               {"interval_seconds": 1, "minimum_delta": 1.0, "resend_interval_seconds": 60},
    "PackCurrent":               {"interval_seconds": 1, "minimum_delta": 0.5, "resend_interval_seconds": 60},
    "RatedRange":                {"interval_seconds": 60, "minimum_delta": 0.5, "resend_interval_seconds": 600},
    "EstBatteryRange":           {"interval_seconds": 60, "minimum_delta": 0.5, "resend_interval_seconds": 600},
    "IdealBatteryRange":         {"interval_seconds": 60, "minimum_delta": 0.5, "resend_interval_seconds": 600},
    "EnergyRemaining":           {"interval_seconds": 30},
    "DetailedChargeState":       {"interval_seconds": 30},
    "ACChargingPower":           {"interval_seconds": 30},
    "DCChargingPower":           {"interval_seconds": 30},
    "ACChargingEnergyIn":        {"interval_seconds": 30},
    "DCChargingEnergyIn":        {"interval_seconds": 30},
    "ChargeAmps":                {"interval_seconds": 30},
    "ChargerVoltage":            {"interval_seconds": 30},
    "ChargeRateMilePerHour":     {"interval_seconds": 30},
    "ChargerPhases":             {"interval_seconds": 60},
    "ChargeLimitSoc":            {"interval_seconds": 60},
    "TimeToFullCharge":          {"interval_seconds": 60},
    "ChargingCableType":         {"interval_seconds": 60},
    "FastChargerPresent":        {"interval_seconds": 60},
    "FastChargerType":           {"interval_seconds": 60},
    "ChargeCurrentRequest":      {"interval_seconds": 30},
    "ChargeCurrentRequestMax":   {"interval_seconds": 60},
    "ChargePortDoorOpen":        {"interval_seconds": 30},
    "ChargePortLatch":           {"interval_seconds": 60},
    "BatteryHeaterOn":           {"interval_seconds": 30},
    "NotEnoughPowerToHeat":      {"interval_seconds": 30},
    "InsideTemp":                {"interval_seconds": 60, "minimum_delta": 0.5, "resend_interval_seconds": 600},
    "OutsideTemp":               {"interval_seconds": 60, "minimum_delta": 0.5, "resend_interval_seconds": 600},
    "HvacACEnabled":             {"interval_seconds": 60},
    "HvacPower":                 {"interval_seconds": 60},
    "HvacFanStatus":             {"interval_seconds": 60},
    "HvacLeftTemperatureRequest":  {"interval_seconds": 60},
    "HvacRightTemperatureRequest": {"interval_seconds": 60},
    "ClimateKeeperMode":         {"interval_seconds": 60},
    "CabinOverheatProtectionMode": {"interval_seconds": 60},
    "PreconditioningEnabled":    {"interval_seconds": 30},
    "DefrostMode":               {"interval_seconds": 30},
    "RearDefrostEnabled":        {"interval_seconds": 30},
    "Odometer":                  {"interval_seconds": 60},
    "Version":                   {"interval_seconds": 3600},
    "SoftwareUpdateVersion":                    {"interval_seconds": 300},
    "SoftwareUpdateInstallationPercentComplete": {"interval_seconds": 60},
    "SoftwareUpdateDownloadPercentComplete":    {"interval_seconds": 60},
    "VehicleName":               {"interval_seconds": 3600},
    "LocatedAtHome":             {"interval_seconds": 60},
    "LocatedAtWork":             {"interval_seconds": 60},
    "LocatedAtFavorite":         {"interval_seconds": 60},
    "Locked":                    {"interval_seconds": 60},
    "SentryMode":                {"interval_seconds": 60},
    "DoorState":                 {"interval_seconds": 60},
    "FdWindow":                  {"interval_seconds": 60},
    "FpWindow":                  {"interval_seconds": 60},
    "RdWindow":                  {"interval_seconds": 60},
    "RpWindow":                  {"interval_seconds": 60},
    "TpmsPressureFl":            {"interval_seconds": 300, "minimum_delta": 0.05, "resend_interval_seconds": 1800},
    "TpmsPressureFr":            {"interval_seconds": 300, "minimum_delta": 0.05, "resend_interval_seconds": 1800},
    "TpmsPressureRl":            {"interval_seconds": 300, "minimum_delta": 0.05, "resend_interval_seconds": 1800},
    "TpmsPressureRr":            {"interval_seconds": 300, "minimum_delta": 0.05, "resend_interval_seconds": 1800},
    "TpmsHardWarnings":          {"interval_seconds": 300},
    "TpmsSoftWarnings":          {"interval_seconds": 300},
    "DestinationName":                       {"interval_seconds": 30},
    "DestinationLocation":                   {"interval_seconds": 30},
    "MilesToArrival":                        {"interval_seconds": 30},
    "MinutesToArrival":                      {"interval_seconds": 30},
    "RouteLastUpdated":                      {"interval_seconds": 30},
    "RouteTrafficMinutesDelay":              {"interval_seconds": 30},
    "ExpectedEnergyPercentAtTripArrival":    {"interval_seconds": 30},
}


DEFAULT_ROSTER = TELEMETRY_FIELDS   # the curated set IS the default ("TeslaMate Complete") profile

# Field -> UI group, for the editor. Every curated field is grouped; anything else falls under "Other".
FIELD_GROUPS = {
    **{k: "Drive & Location" for k in ("VehicleSpeed", "Location", "GpsHeading", "Gear", "Odometer")},
    **{k: "Battery & Charging" for k in (
        "Soc", "BatteryLevel", "EnergyRemaining", "RatedRange", "EstBatteryRange", "IdealBatteryRange",
        "PackVoltage", "PackCurrent", "DetailedChargeState", "ACChargingPower", "DCChargingPower",
        "ACChargingEnergyIn", "DCChargingEnergyIn", "ChargeAmps", "ChargerVoltage", "ChargeRateMilePerHour",
        "ChargerPhases", "ChargeLimitSoc", "TimeToFullCharge", "ChargingCableType", "FastChargerPresent",
        "FastChargerType", "ChargeCurrentRequest", "ChargeCurrentRequestMax", "ChargePortDoorOpen",
        "ChargePortLatch", "BatteryHeaterOn", "NotEnoughPowerToHeat")},
    **{k: "Climate" for k in (
        "InsideTemp", "OutsideTemp", "HvacACEnabled", "HvacPower", "HvacFanStatus",
        "HvacLeftTemperatureRequest", "HvacRightTemperatureRequest", "ClimateKeeperMode",
        "CabinOverheatProtectionMode", "PreconditioningEnabled", "DefrostMode", "RearDefrostEnabled")},
    **{k: "Body & Security" for k in (
        "Locked", "SentryMode", "DoorState", "FdWindow", "FpWindow", "RdWindow", "RpWindow", "VehicleName")},
    **{k: "Tires" for k in (
        "TpmsPressureFl", "TpmsPressureFr", "TpmsPressureRl", "TpmsPressureRr",
        "TpmsHardWarnings", "TpmsSoftWarnings")},
    **{k: "Software" for k in (
        "Version", "SoftwareUpdateVersion", "SoftwareUpdateInstallationPercentComplete",
        "SoftwareUpdateDownloadPercentComplete")},
    **{k: "Navigation" for k in (
        "DestinationName", "DestinationLocation", "MilesToArrival", "MinutesToArrival",
        "RouteLastUpdated", "RouteTrafficMinutesDelay", "ExpectedEnergyPercentAtTripArrival")},
    **{k: "Geofence" for k in ("LocatedAtHome", "LocatedAtWork", "LocatedAtFavorite")},
}

# Fields TeslaMate needs for correct drives/charges/power — disabling these silently breaks it.
ESSENTIAL_FIELDS = frozenset((
    "Location", "Soc", "BatteryLevel", "Gear", "DetailedChargeState", "Odometer", "VehicleSpeed",
    "PackVoltage", "PackCurrent"))

# Every Tesla telemetry Field (proto enum), for the editor's "show all" power-user view. The curated
# roster is a subset; the rest only reach raw Logger/MQTT/Pub-Sub (not the dashboard/TeslaMate shim).
ALL_FIELDS = (
    "DriveRail", "ChargeState", "BmsFullchargecomplete", "VehicleSpeed", "Odometer", "PackVoltage",
    "PackCurrent", "Soc", "DCDCEnable", "Gear", "IsolationResistance", "PedalPosition", "BrakePedal",
    "DiStateR", "DiHeatsinkTR", "DiAxleSpeedR", "DiTorquemotor", "DiStatorTempR", "DiVBatR",
    "DiMotorCurrentR", "Location", "GpsState", "GpsHeading", "NumBrickVoltageMax", "BrickVoltageMax",
    "NumBrickVoltageMin", "BrickVoltageMin", "NumModuleTempMax", "ModuleTempMax", "NumModuleTempMin",
    "ModuleTempMin", "RatedRange", "Hvil", "DCChargingEnergyIn", "DCChargingPower", "ACChargingEnergyIn",
    "ACChargingPower", "ChargeLimitSoc", "FastChargerPresent", "EstBatteryRange", "IdealBatteryRange",
    "BatteryLevel", "TimeToFullCharge", "ScheduledChargingStartTime", "ScheduledChargingPending",
    "ScheduledDepartureTime", "PreconditioningEnabled", "ScheduledChargingMode", "ChargeAmps",
    "ChargeEnableRequest", "ChargerPhases", "ChargePortColdWeatherMode", "ChargeCurrentRequest",
    "ChargeCurrentRequestMax", "BatteryHeaterOn", "NotEnoughPowerToHeat", "SuperchargerSessionTripPlanner",
    "DoorState", "Locked", "FdWindow", "FpWindow", "RdWindow", "RpWindow", "VehicleName", "SentryMode",
    "SpeedLimitMode", "CurrentLimitMph", "Version", "TpmsPressureFl", "TpmsPressureFr", "TpmsPressureRl",
    "TpmsPressureRr", "TpmsLastSeenPressureTimeFl", "TpmsLastSeenPressureTimeFr",
    "TpmsLastSeenPressureTimeRl", "TpmsLastSeenPressureTimeRr", "InsideTemp", "OutsideTemp",
    "SeatHeaterLeft", "SeatHeaterRight", "SeatHeaterRearLeft", "SeatHeaterRearRight",
    "SeatHeaterRearCenter", "AutoSeatClimateLeft", "AutoSeatClimateRight", "DriverSeatBelt",
    "PassengerSeatBelt", "DriverSeatOccupied", "LateralAcceleration", "LongitudinalAcceleration",
    "CruiseSetSpeed", "LifetimeEnergyUsed", "LifetimeEnergyUsedDrive", "BrakePedalPos", "RouteLastUpdated",
    "RouteLine", "MilesToArrival", "MinutesToArrival", "OriginLocation", "DestinationLocation", "CarType",
    "Trim", "ExteriorColor", "RoofColor", "ChargePort", "ChargePortLatch", "GuestModeEnabled",
    "PinToDriveEnabled", "PairedPhoneKeyAndKeyFobQty", "CruiseFollowDistance", "AutomaticBlindSpotCamera",
    "BlindSpotCollisionWarningChime", "SpeedLimitWarning", "ForwardCollisionWarning",
    "LaneDepartureAvoidance", "EmergencyLaneDepartureAvoidance", "AutomaticEmergencyBrakingOff",
    "LifetimeEnergyGainedRegen", "EnergyRemaining", "ServiceMode", "BMSState",
    "GuestModeMobileAccessState", "DestinationName", "DetailedChargeState", "CabinOverheatProtectionMode",
    "CabinOverheatProtectionTemperatureLimit", "CenterDisplay", "ChargePortDoorOpen", "ChargerVoltage",
    "ChargingCableType", "ClimateKeeperMode", "DefrostForPreconditioning", "DefrostMode",
    "EfficiencyPackage", "EstimatedHoursToChargeTermination", "EuropeVehicle",
    "ExpectedEnergyPercentAtTripArrival", "FastChargerType", "HomelinkDeviceCount", "HomelinkNearby",
    "HvacACEnabled", "HvacAutoMode", "HvacFanSpeed", "HvacFanStatus", "HvacLeftTemperatureRequest",
    "HvacPower", "HvacRightTemperatureRequest", "HvacSteeringWheelHeatAuto", "HvacSteeringWheelHeatLevel",
    "OffroadLightbarPresent", "PowershareHoursLeft", "PowershareInstantaneousPowerKW", "PowershareStatus",
    "PowershareStopReason", "PowershareType", "RearDisplayHvacEnabled", "RearSeatHeaters",
    "RemoteStartEnabled", "RightHandDrive", "RouteTrafficMinutesDelay",
    "SoftwareUpdateDownloadPercentComplete", "SoftwareUpdateExpectedDurationMinutes",
    "SoftwareUpdateInstallationPercentComplete", "SoftwareUpdateScheduledStartTime",
    "SoftwareUpdateVersion", "TonneauOpenPercent", "TonneauPosition", "TonneauTentMode",
    "TpmsHardWarnings", "TpmsSoftWarnings", "ValetModeEnabled", "WheelType", "WiperHeatEnabled",
    "LocatedAtHome", "LocatedAtWork", "LocatedAtFavorite", "SettingDistanceUnit", "SettingTemperatureUnit",
    "Setting24HourTime", "SettingTirePressureUnit", "SettingChargeUnit", "ClimateSeatCoolingFrontLeft",
    "ClimateSeatCoolingFrontRight", "LightsHazardsActive", "LightsTurnSignal", "LightsHighBeams",
    "MediaPlaybackStatus", "MediaPlaybackSource", "MediaAudioVolume", "MediaNowPlayingDuration",
    "MediaNowPlayingElapsed", "MediaNowPlayingArtist", "MediaNowPlayingTitle", "MediaNowPlayingAlbum",
    "MediaNowPlayingStation", "MediaAudioVolumeIncrement", "MediaAudioVolumeMax", "SunroofInstalled",
    "SeatVentEnabled", "RearDefrostEnabled", "ChargeRateMilePerHour", "MilesSinceReset",
    "SelfDrivingMilesSinceReset",
)


def _low_bandwidth_roster():
    """Essentials only, intervals relaxed to >= 30 s — a minimal, low-volume profile."""
    return {k: {"interval_seconds": max(30, DEFAULT_ROSTER.get(k, {}).get("interval_seconds", 30))}
            for k in ESSENTIAL_FIELDS}


# Presets the editor offers. "custom" = a user override is in effect (no fixed dict).
PROFILES = {
    "teslamate": DEFAULT_ROSTER,
    "low_bandwidth": _low_bandwidth_roster(),
}


def effective_roster(override=None):
    """The roster actually sent to the car: DEFAULT_ROSTER overlaid by the user override. `override` is
    {Name: {enabled: bool, interval_seconds: int}}. Returns {Name: {interval_seconds}} of ENABLED fields.
    Fields absent from the override keep their default (enabled). Disabled fields are dropped; fields the
    override adds (e.g. from 'show all') are included when enabled."""
    override = override or {}
    out = {}
    for name in set(TELEMETRY_FIELDS) | set(override):
        o = override.get(name)
        if o is None:                       # untouched default field -> keep as default
            out[name] = dict(TELEMETRY_FIELDS[name])
            continue
        if not o.get("enabled", True):      # explicitly disabled
            continue
        default_iv = TELEMETRY_FIELDS.get(name, {}).get("interval_seconds", 60)
        try:
            iv = int(o.get("interval_seconds", default_iv))
        except (TypeError, ValueError):
            iv = default_iv
        entry = {"interval_seconds": max(1, iv)}
        # Preserve the on-change keys: an explicit override value wins, else the field's default.
        for k in ("minimum_delta", "resend_interval_seconds"):
            v = o.get(k, TELEMETRY_FIELDS.get(name, {}).get(k))
            if v is not None:
                entry[k] = v
        out[name] = entry
    return out


def telemetry_fields_hash(roster=None):
    """Stable fingerprint of a requested-field roster (defaults to DEFAULT_ROSTER). Used to auto-resend
    fleet_telemetry_config when (and only when) the roster changes — across an upgrade or a user edit."""
    import hashlib
    import json as _json
    return hashlib.sha256(_json.dumps(TELEMETRY_FIELDS if roster is None else roster,
                                      sort_keys=True).encode()).hexdigest()
