#!/usr/bin/env python3
"""Pure builders for TeslaMate ``data:update`` streaming frames.

The live app serves the TeslaMate streaming ws via ``app/sinks/stream.py`` (StreamSink), which reads
the shared Store and calls ``build_data_update`` here. These functions carry no state and do no I/O —
they're the verified CSV-frame transforms, kept in one place so the sink and its tests share them.

The ``data:update`` value is a CSV in the exact column order TeslaMate's stream parser expects:
  time_ms, speed, odometer, soc, elevation, est_heading, est_lat, est_lng, power, shift_state,
  range, est_range, heading
A frame is only emitted once Latitude, Longitude and Gear have all been seen for the VIN.
"""
from datetime import datetime

import fields


def _epoch_ms(created_at):
    if isinstance(created_at, (int, float)):
        return int(created_at * 1000)   # keep ms precision; truncating to whole seconds broke regen-over-dt
    s = str(created_at).replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(s).timestamp() * 1000)
    except ValueError:
        return 0


def _int_or_blank(v):
    n = fields.num(v)
    return "" if n is None else int(n)


def _blank_if_none(v):
    return "" if v is None else v


def build_data_update(vin, last_values, created_at, elevation=None):
    """Build a TeslaMate ``data:update`` message, or None if position/gear aren't known yet.

    ``elevation`` (meters) fills the column that Tesla's Fleet API/Telemetry no longer provide; it is
    resolved from a local DEM by the sink (None -> blank, the column's prior always-empty behavior)."""
    lv = last_values.get(vin, {})
    # Gear must have been *seen* at least once (key present) — but once seen it can be None, which is
    # how a park reads: Tesla streams Gear="<invalid>" on park, strip_state -> None. We must still emit
    # that frame (shift_state="") so TeslaMate sees the drive END; suppressing it strands the drive.
    if lv.get("Latitude") is None or lv.get("Longitude") is None or "Gear" not in lv:
        return None
    # Drive power (kW). There is NO streamed "Power" field — it's not in the requested roster — so the
    # old `lv.get("Power")` always read None and every driving frame emitted power=0, killing
    # positions.power and drives.power_max/power_min/regen on the streaming path. Compute it the same
    # way the REST shim does: -PackVoltage*PackCurrent/1000, gated on driving (0 when parked) so
    # charging/idle frames don't inject phantom motor power. PackVoltage/PackCurrent are streamed.
    power = 0
    pv, pc = fields.num(lv.get("PackVoltage")), fields.num(lv.get("PackCurrent"))
    if lv.get("Gear") in ("D", "R", "N") and pv is not None and pc is not None:
        power = int(round(-pv * pc / 1000.0))
    # VehicleSpeed is an on-change field: it stops streaming when the car parks, so lv retains the
    # last *driving* value indefinitely. Gate on driving (like the REST shim's assemble()) so a parked
    # stream frame doesn't carry a stale speed. NOTE: this does NOT clear sensor.tesla_speed — TeslaMate
    # skips a nil 'speed' on its retained MQTT topic (speed is not in @publish_if_nil), so the retained
    # last value persists. Clearing is done by the REST shim reporting speed=0 on park (shim_data.py).
    speed = _int_or_blank(lv.get("VehicleSpeed")) if lv.get("Gear") in ("D", "R", "N") else ""
    value = ",".join(str(x) for x in [
        _epoch_ms(created_at),
        speed,
        _blank_if_none(lv.get("Odometer")),
        _int_or_blank(lv.get("Soc")),
        _int_or_blank(elevation),                # elevation (m) from local DEM, "" if unresolved
        _blank_if_none(lv.get("GpsHeading")),    # est_heading
        lv["Latitude"],                          # est_lat
        lv["Longitude"],                         # est_lng
        power,
        _blank_if_none(lv.get("Gear")),          # shift_state ("" when parked / Gear=<invalid>)
        _blank_if_none(lv.get("RatedRange")),    # range
        _blank_if_none(lv.get("EstBatteryRange")),  # est_range
        _blank_if_none(lv.get("GpsHeading")),    # heading
    ])
    return {"msg_type": "data:update", "tag": vin, "value": value}
