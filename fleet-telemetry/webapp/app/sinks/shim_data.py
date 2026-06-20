"""Fleet-API ``vehicle_data`` assembly for the TeslaMate shim sink.

Ported verbatim (behavior-preserving) from the v0 shim's Vehicle._assemble / vehicle_data, but as
pure functions that read a plain {field: value} snapshot from the unified Store instead of carrying
their own per-process state. Charge-session baseline tracking is passed in by the sink.
"""
import fields as F


def energy_in(f):
    """Battery-stored (DC) energy, falling back to AC wall draw before DC arrives."""
    dc = F.num(f.get("DCChargingEnergyIn"))
    if dc is not None:
        return dc
    return F.num(f.get("ACChargingEnergyIn")) or 0.0


def charging_active(f):
    cs = F.strip_state(f.get("DetailedChargeState")) or F.strip_state(f.get("ChargeState"))
    return cs in ("Charging", "Starting")


def assemble(f, *, ts, identity, charge_baseline=None):
    lat, lon = F.parse_location(f.get("Location"))
    gear = F.strip_state(f.get("Gear"))
    shift = gear if gear in ("D", "R", "N") else None
    driving = shift in ("D", "R", "N")
    pv, pc = F.num(f.get("PackVoltage")), F.num(f.get("PackCurrent"))
    power = int(round(-pv * pc / 1000.0)) if (driving and pv is not None and pc is not None) else (None if driving else 0)
    dest_lat, dest_lon = F.parse_location(f.get("DestinationLocation"))
    drive_state = {"timestamp": ts, "latitude": lat, "longitude": lon, "heading": F.num(f.get("GpsHeading")),
                   # Parked -> 0, NOT None: TeslaMate's MQTT publisher skips a nil speed on a retained
                   # topic (speed is not in @publish_if_nil), so a null would leave sensor.tesla_speed
                   # stuck at the last driving value. 0 clears it; shift_state (not speed) gates drives.
                   "speed": F.num(f.get("VehicleSpeed")) if driving else 0, "power": power, "shift_state": shift,
                   "active_route_destination": f.get("DestinationName") or f.get("Destination") or None,
                   "active_route_latitude": dest_lat, "active_route_longitude": dest_lon,
                   "active_route_miles_to_arrival": F.num(f.get("MilesToArrival")),
                   "active_route_minutes_to_arrival": F.num(f.get("MinutesToArrival")),
                   "active_route_energy_at_arrival": F.round_int(f.get("ExpectedEnergyPercentAtTripArrival")),
                   "active_route_traffic_minutes_delay": F.num(f.get("RouteTrafficMinutesDelay"))}

    ac_p, dc_p = F.num(f.get("ACChargingPower")), F.num(f.get("DCChargingPower"))
    charger_power = int(round((ac_p or 0) + (dc_p or 0))) if (ac_p is not None or dc_p is not None) else None
    energy_added = round(energy_in(f) - charge_baseline, 3) if charge_baseline is not None else None
    charge_state = {
        "timestamp": ts,
        "charging_state": F.strip_state(f.get("DetailedChargeState")) or F.strip_state(f.get("ChargeState")),
        "battery_level": F.round_int(f.get("Soc") if F.num(f.get("Soc")) is not None else f.get("BatteryLevel")),
        "usable_battery_level": F.round_int(f.get("BatteryLevel") if F.num(f.get("BatteryLevel")) is not None else f.get("Soc")),
        "battery_range": F.num(f.get("RatedRange")), "est_battery_range": F.num(f.get("EstBatteryRange")),
        "ideal_battery_range": F.num(f.get("IdealBatteryRange")), "charge_energy_added": energy_added,
        "charger_actual_current": F.round_int(f.get("ChargeAmps")), "charger_phases": F.round_int(f.get("ChargerPhases")),
        "charger_power": charger_power, "charger_voltage": F.round_int(f.get("ChargerVoltage")),
        "conn_charge_cable": F.strip_enum(f.get("ChargingCableType")),
        "fast_charger_present": f.get("FastChargerPresent") if isinstance(f.get("FastChargerPresent"), bool) else None,
        "fast_charger_type": F.strip_enum(f.get("FastChargerType")), "time_to_full_charge": F.num(f.get("TimeToFullCharge")),
        "charge_limit_soc": F.round_int(f.get("ChargeLimitSoc")),
        "charge_current_request": F.round_int(f.get("ChargeCurrentRequest")),
        "charge_current_request_max": F.round_int(f.get("ChargeCurrentRequestMax")),
        "charger_pilot_current": F.round_int(f.get("ChargerPilotCurrent")),   # Fleet-API only (seeded)
        "fast_charger_brand": F.strip_enum(f.get("FastChargerBrand")),        # Fleet-API only (seeded)
        "charge_port_door_open": f.get("ChargePortDoorOpen") if isinstance(f.get("ChargePortDoorOpen"), bool) else None,
        "battery_heater_on": f.get("BatteryHeaterOn") if isinstance(f.get("BatteryHeaterOn"), bool) else None,
        "not_enough_power_to_heat": f.get("NotEnoughPowerToHeat") if isinstance(f.get("NotEnoughPowerToHeat"), bool) else None,
        "charge_rate": F.num(f.get("ChargeRateMilePerHour")),
        "charge_port_latch": F.strip_state(f.get("ChargePortLatch"))}

    climate_state = {
        "timestamp": ts, "outside_temp": F.num(f.get("OutsideTemp")), "inside_temp": F.num(f.get("InsideTemp")),
        "is_climate_on": (F.as_bool(f.get("HvacACEnabled")) or F.strip_state(f.get("HvacPower")) == "On") or None,
        "climate_keeper_mode": (lambda m: m.lower() if m else None)(F.strip_state(f.get("ClimateKeeperMode"))),
        "fan_status": F.fan_speed(f.get("HvacFanStatus")), "driver_temp_setting": F.num(f.get("HvacLeftTemperatureRequest")),
        "passenger_temp_setting": F.num(f.get("HvacRightTemperatureRequest")),
        "is_preconditioning": f.get("PreconditioningEnabled") if isinstance(f.get("PreconditioningEnabled"), bool) else None,
        "is_front_defroster_on": F.defrost_on(f.get("DefrostMode")),
        "is_rear_defroster_on": f.get("RearDefrostEnabled") if isinstance(f.get("RearDefrostEnabled"), bool) else None,
        "battery_heater": f.get("BatteryHeaterOn") if isinstance(f.get("BatteryHeaterOn"), bool) else None,
        "battery_heater_no_power": f.get("NotEnoughPowerToHeat") if isinstance(f.get("NotEnoughPowerToHeat"), bool) else None,
        "cabin_overheat_protection": F.strip_state(f.get("CabinOverheatProtectionMode"))}

    sentry = F.strip_state(f.get("SentryMode"))
    vehicle_state = {
        "timestamp": ts, "odometer": F.num(f.get("Odometer")),
        "car_version": f.get("Version") if isinstance(f.get("Version"), str) else None,
        "locked": f.get("Locked") if isinstance(f.get("Locked"), bool) else None,
        "sentry_mode": (sentry in ("Armed", "On", "Enabled")) if sentry is not None else None,
        "tpms_pressure_fl": F.num(f.get("TpmsPressureFl")), "tpms_pressure_fr": F.num(f.get("TpmsPressureFr")),
        "tpms_pressure_rl": F.num(f.get("TpmsPressureRl")), "tpms_pressure_rr": F.num(f.get("TpmsPressureRr")),
        "fd_window": F.window_state(f.get("FdWindow")), "fp_window": F.window_state(f.get("FpWindow")),
        "rd_window": F.window_state(f.get("RdWindow")), "rp_window": F.window_state(f.get("RpWindow")),
        "is_user_present": False,
        "vehicle_name": f.get("VehicleName") if isinstance(f.get("VehicleName"), str) else None,
        "software_update": {"status": "", "download_perc": 0, "install_perc": 0, "version": ""}}
    doors = f.get("DoorState") if isinstance(f.get("DoorState"), dict) else None
    if doors is not None:
        vehicle_state.update({"df": 1 if doors.get("DriverFront") else 0, "pf": 1 if doors.get("PassengerFront") else 0,
                              "dr": 1 if doors.get("DriverRear") else 0, "pr": 1 if doors.get("PassengerRear") else 0,
                              "ft": 1 if doors.get("TrunkFront") else 0, "rt": 1 if doors.get("TrunkRear") else 0})

    vehicle_config = f.get("VehicleConfig") if isinstance(f.get("VehicleConfig"), dict) else {}
    out = {**identity, "drive_state": drive_state, "charge_state": charge_state,
           "climate_state": climate_state, "vehicle_state": vehicle_state, "vehicle_config": vehicle_config}
    out.setdefault("state", "online")   # identity carries the authoritative state; default for bare callers
    return out


def vehicle_data(f, *, ts, identity, charge_baseline=None):
    """Assemble the full vehicle_data from the unified snapshot `f` (telemetry overlaid on the Fleet
    seed). No prime-merge here anymore — the Store is the single source of truth."""
    tele = assemble(f, ts=ts, identity=identity, charge_baseline=charge_baseline)
    if tele["charge_state"].get("charging_state") is None:
        tele["charge_state"]["charging_state"] = "Disconnected"
    if tele["drive_state"].get("power") is None:
        tele["drive_state"]["power"] = 0
    return tele
