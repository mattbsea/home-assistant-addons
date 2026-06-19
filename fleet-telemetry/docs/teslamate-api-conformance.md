# Fleet Telemetry → TeslaMate API Conformance Review

Does the Fleet Telemetry add-on supply TeslaMate everything it persists, in the unit/format/shape
TeslaMate expects? This checks the add-on's two TeslaMate-facing surfaces against
[`teslamate-data-dictionary.md`](./teslamate-data-dictionary.md) (TeslaMate v4.0.1).

- **Add-on version reviewed:** v1.0.15 (current `main`).
- **Surfaces in scope:** (1) REST shim (`app/sinks/shim_data.py`, `app/sinks/shim_rest.py`,
  `fields.prime_to_fields`); (2) Streaming WS (`ws_stream.py` driven by `app/sinks/stream.py`).
  MQTT is TeslaMate's *own* egress, so it's out of scope.
- **Evidence basis:** add-on code (`file:line`), the dictionary, the TeslaMate v4.0.1 clone, and live
  data from `/data/telemetry-log.jsonl` + `/data/fleet-log.jsonl` + the `teslamate` Postgres.
- **Guiding principle:** TeslaMate expects **Tesla-native imperial input** (speed=mph,
  ranges/odometer=miles, temps=°C, power/charger_power=kW, pressure=bar, energy=kWh) and does its own
  SI conversion. The add-on must emit native units, never pre-converted SI.

## Executive summary

**Overall: conformant.** Units, formats, enum strings, JSON/CSV shapes, and the streaming column
order all match TeslaMate's expectations. One functional defect (fixed); **no missing coverage** —
every column TeslaMate persists is supplied (verified against the live `teslamate` DB).

> **Update (v1.0.16):** F1 (streaming power) and F5 (cable/charger-type enum) are **fixed**, confirmed
> against a captured drive (see [§Ground truth](#ground-truth-2026-06-19-drive)).
>
> **Correction (DB-verified):** the original F2 finding ("3 columns never supplied / always NULL") was
> **wrong** — it inferred from `assemble()` and the telemetry roster, missing the generic prime-backfill
> in `shim_data.vehicle_data` that copies Fleet-API `charge_state`/`climate_state` keys straight
> through. Live DB shows `charger_pilot_current` and `fast_charger_brand` are already supplied.

| Severity | Finding | Surface | Impact | Status |
|---|---|---|---|---|
| **High (isolated)** | F1 — `power` is hard-zeroed on the streaming path | Streaming | Stream-fed `positions.power` = 0; `drives.power_max/power_min` and **regen** lost during drives | **fixed v1.0.16** |
| Low | F5 — `ChargingCableType`/`FastChargerType` prefix not stripped (`CableTypeSAE`) | REST | TeslaMate stored `"CableTypeSAE"` not `"SAE"` | **fixed v1.0.16** |
| Info | F2a — `charges.charger_pilot_current` | REST | **already supplied** — DB 71,606/71,606 non-null (=32 A) via prime-backfill | not a gap |
| Info | F2b — `charges.fast_charger_brand` | REST | **already supplied** when present — DB 71,101 non-null; null only when Fleet API returns `"<invalid>"` (AC charging), correctly skipped | not a gap |
| Low | F2c — `positions/charges.battery_heater_no_power` | REST | mapping exists (prime-backfill) but **prime-only, not live**; DB 0 non-null because Fleet API returns `null` (battery never power-constrained) | optional: add a live `NotEnoughPowerToHeat → climate_state` mapping |
| Low (latent) | F3 — gear normalized with `strip_state`, not `gear_letter` | REST+stream | only fails on `DriveGear*`/word forms — **not observed** (drive emitted only `ShiftState*`/`<invalid>`) | deferred (naive swap unsafe) |
| Info | F4 — REST `vehicle_data` carries no `heading` key | REST | `positions.heading` filled only via streaming `est_heading` (by design) | by design |

**Confirmed correct (high-value):** units are native imperial end-to-end (live: telemetry
`RatedRange`≈126.4 mi vs Fleet API `battery_range`=126.83 mi → **miles**, no double-conversion);
REST `power` sign + kW math verified (`-PackVoltage*PackCurrent/1000`, +drive/−regen); streaming CSV
column order is an exact 13/13 match; charge-energy session-baseline math correct; ephemeral
gear/speed prime-skip guards intact.

---

## 1. REST `vehicle_data` conformance

Every TeslaMate-persisted field reachable via the REST poll (the `positions`/`charges` columns). All
emitted in native imperial; TeslaMate converts. Source: `shim_data.assemble` / `vehicle_data`.

### drive_state → positions
| TeslaMate col | expected unit | supplied (telemetry src) | verdict | evidence |
|---|---|---|---|---|
| latitude / longitude | ° | `Location` → `parse_location` | OK | shim_data.py:24; fields.py:25-36 |
| speed | mph | `VehicleSpeed` (driving-gated) | OK | shim_data.py:31 |
| power | kW (±) | `-PackVoltage*PackCurrent/1000` | OK (sign verified) | shim_data.py:28-29,117 |
| shift_state | P/R/N/D\|null | `Gear` → `strip_state` | OK (latent: see L4) | shim_data.py:25-26 |
| odometer (vehicle_state) | miles | `Odometer` | OK | shim_data.py:74 |

### charge_state → positions + charges
| TeslaMate col | expected unit | supplied (telemetry src) | verdict | evidence |
|---|---|---|---|---|
| battery_level / usable_battery_level | % | `Soc` / `BatteryLevel` | OK | shim_data.py:42-43 |
| ideal/est/rated_battery_range_km | miles | `IdealBatteryRange`/`EstBatteryRange`/`RatedRange`(→`battery_range`) | OK | shim_data.py:44-45 |
| charge_energy_added | kWh | `DC/ACChargingEnergyIn` − session baseline | OK | shim_data.py:10-15,38; state.py:97-111 |
| charger_power | kW | `ACChargingPower`+`DCChargingPower` | OK | shim_data.py:36-37 |
| charger_voltage / charger_actual_current / charger_phases | V / A / count | `ChargerVoltage` / `ChargeAmps` / `ChargerPhases` | OK | shim_data.py:46-47 |
| charger_pilot_current | A | Fleet-API prime (`charge.charger_pilot_current`) → prime-backfill | **OK — DB 71,606/71,606 non-null** | shim_data.py:103-114; vehicle.ex:1631 |
| conn_charge_cable / fast_charger_type | string | `ChargingCableType` / `FastChargerType` → `strip_enum` | OK | shim_data.py:48,50 |
| fast_charger_present | bool | `FastChargerPresent` | OK | shim_data.py:49 |
| fast_charger_brand | string | Fleet-API prime (`charge.fast_charger_brand`) → prime-backfill | **OK when present — DB 71,101 non-null** (skipped when `"<invalid>"`) | shim_data.py:103-114; vehicle.ex:1636 |
| not_enough_power_to_heat | bool | `NotEnoughPowerToHeat` | OK (rarely emitted) | shim_data.py:56 |
| battery_heater_on | bool | `BatteryHeaterOn` | OK | shim_data.py:55 |
| charging_state (FSM) | "Charging"/"Starting"/"Complete"/"Disconnected" | `DetailedChargeState`/`ChargeState` → `strip_state`; None→"Disconnected" | OK | shim_data.py:41,115 |

### climate_state → positions (+charges)
| TeslaMate col | expected | supplied | verdict | evidence |
|---|---|---|---|---|
| outside_temp / inside_temp | °C | `OutsideTemp` / `InsideTemp` | OK | shim_data.py:61 |
| driver_temp_setting / passenger_temp_setting | °C | `HvacLeft/RightTemperatureRequest` | OK | shim_data.py:64-65 |
| fan_status | 0–7 | `HvacFanStatus` → `fan_speed` | OK | shim_data.py:64; fields.py:63-73 |
| is_climate_on | bool | `HvacACEnabled`/`HvacPower` | OK | shim_data.py:62 |
| is_front/rear_defroster_on | bool | `DefrostMode` / `RearDefrostEnabled` | OK | shim_data.py:67-68 |
| battery_heater | bool | `BatteryHeaterOn` | OK | shim_data.py:69 |
| battery_heater_no_power | bool | Fleet-API prime (`climate.battery_heater_no_power`) → prime-backfill | supplied-when-present (DB 0 non-null: Fleet API returns `null`; **prime-only, not live**) | shim_data.py:103-114; vehicle.ex:1591,1625 |

### vehicle_state → positions
| TeslaMate col | expected | supplied | verdict | evidence |
|---|---|---|---|---|
| tpms_pressure_fl/fr/rl/rr | bar | `TpmsPressure*` | OK | shim_data.py:78-79 |
| (elevation) | m | — by design TeslaMate uses its own SRTM for REST cars | n/a | dict §6/§7 |

**Cold-start fill:** before the live stream warms up, `fields.prime_to_fields` populates slow fields
from the Fleet-API snapshot, guarded by `_PRIME_BACKFILL_SKIP={drive_state:(shift_state,speed)}`
(shim_data.py:100) and `PRIME_EPHEMERAL_FIELDS=(Gear,VehicleSpeed)` (state.py:21) so stale prime data
never makes TeslaMate report phantom driving. Units stay native through this path.

---

## 2. Streaming `data:update` conformance

CSV emit order (`ws_stream.py:91-105`) vs TeslaMate's `[:time | @columns]` (`stream.ex:18-19`,
zipped at `:126` — exact length is load-bearing). **13/13 exact match.**

| Pos | Column | expected (unit) | TeslaMate uses? | supplied (src) | verdict | evidence |
|---|---|---|---|---|---|---|
| 0 | time | unix ms | yes → positions.date | `_epoch_ms(CreatedAt)` | OK | ws_stream.py:92 |
| 1 | speed | mph | yes → speed (mph→km/h) | `VehicleSpeed` (driving-gated; ""=parked) | OK | ws_stream.py:90,93 |
| 2 | odometer | miles | yes → odometer | `Odometer` | OK | ws_stream.py:94 |
| 3 | soc | % | yes → battery_level | `Soc` | OK | ws_stream.py:95 |
| 4 | elevation | m | yes → elevation | local DEM (`elevation.Resolver`) | OK | ws_stream.py:96; stream.py:73 |
| 5 | est_heading | ° | yes → drive_state.heading (merge) | `GpsHeading` | OK | ws_stream.py:97 |
| 6 | est_lat | ° | yes → latitude | `Latitude` | OK | ws_stream.py:98 |
| 7 | est_lng | ° | yes → longitude | `Longitude` | OK | ws_stream.py:99 |
| 8 | **power** | kW (±) | yes → positions.power | `lv.get("Power")` (**never set**) + DC/AC if >0 | **FAIL → emits 0** | ws_stream.py:77-84,100 |
| 9 | shift_state | P/R/N/D\|"" | yes → drive FSM | `Gear` → `strip_state` | OK | ws_stream.py:101 |
| 10 | range | miles | **no (dropped)** | `RatedRange` | inert | ws_stream.py:102 |
| 11 | est_range | miles | **no (dropped)** | `EstBatteryRange` | inert | ws_stream.py:103 |
| 12 | heading | ° | **no (dropped)** | `GpsHeading` | inert | ws_stream.py:104 |

Columns 10–12 are decoded-then-discarded by TeslaMate (`stream/data.ex` parses them but nothing
reads `stream_data.range`/`.est_range`, and `merge` uses `est_heading` not raw `heading`), so the
add-on populating them is harmless. Battery range persists only via the REST path.

---

## 3. Cross-path coverage

With `use_streaming_api=true` (this deployment's setting), TeslaMate inserts the high-frequency
driving `positions` from the **stream**; the REST poll covers parked/charging polls and the broader
column set. Consequences:

| Field | REST | Streaming | Net for drive positions |
|---|---|---|---|
| location, speed, odometer, soc, elevation, shift_state | ✔ correct | ✔ correct | OK |
| **power** | ✔ computed (kW, ±) | ✘ **0** | **mostly 0** (stream dominates during drives) |
| temps, ranges, usable SoC, tpms, charge fields | ✔ | ✘ not on stream | filled by interleaved REST polls only |

---

## 4. Findings

### F1 — Streaming `power` is always 0 during drives (High, isolated)
`build_data_update` reads `lv.get("Power")` (ws_stream.py:78), but **`Power` is not in
`fields.TELEMETRY_FIELDS`** — the car never streams a field by that name. The DC/AC override only
fires when charging power > 0. So every driving stream frame emits `power=0` (a literal 0, not blank).
- **Live evidence:** `Power` occurrences in the telemetry log = **0**; `PackVoltage`/`PackCurrent` =
  **237 each**. The inputs to compute power are present and streaming.
- **Impact:** stream-fed `positions.power` = 0 → `drives.power_max`/`power_min` ≈ 0 and **regen
  (negative power) is never captured** for the high-frequency drive samples. Only the sparser
  interleaved REST positions carry real power.
- **Why REST is fine but stream isn't:** the REST shim computes `-PackVoltage*PackCurrent/1000`
  (shim_data.py:28-29); the stream path never does. The fix is in-band (same inputs already stream).

### F2 — Three "diagnostic" columns — CORRECTED: two already supplied, one prime-only (Low)
> The original finding ("never supplied / always NULL") was **wrong**. It inferred from
> `shim_data.assemble()` and the telemetry roster, missing the **generic prime-backfill** in
> `shim_data.vehicle_data` (`shim_data.py:103-114`): after `assemble()`, it iterates every key in the
> Fleet-API prime's `charge_state`/`climate_state`/etc. and copies it into the output wherever the
> assembled value is None (skipping `"<invalid>"`). So Fleet-API-only fields reach TeslaMate even
> though `assemble()` never names them. Verified against the **live `teslamate` DB**, the captured
> fleet-log Fleet-API responses, and the Fleet Telemetry `vehicle_data.proto`:

- **`charger_pilot_current`** (charges): **already supplied.** DB: **71,606 / 71,606 non-null**
  (current value 32 A, matching `charger_actual_current`). Not in the telemetry proto (only
  `ChargeAmps`/`ChargeCurrentRequest`/`ChargeCurrentRequestMax`), but the Fleet API returns it
  (fleet-log: 32/48 A) and the prime-backfill passes it through. **Not a gap.**
- **`fast_charger_brand`** (charges): **already supplied when present.** DB: **71,101 non-null.** Null
  only when the Fleet API returns `"<invalid>"` (AC charging — fleet-log confirms), which the
  prime-backfill correctly skips; a real DC brand (e.g. "Tesla") flows through. **Not a gap.**
- **`battery_heater_no_power`** (positions+charges): mapping exists via the prime-backfill, but the
  Fleet API returns `null` (this battery has never been power-constrained — DB: 0 non-null), so there
  is nothing to record yet. It is **prime-only, not live**: the streamed `NotEnoughPowerToHeat`
  (proto field 56, in roster) feeds `charge_state.not_enough_power_to_heat` but not the climate
  `battery_heater_no_power`. *Optional* improvement: also map `NotEnoughPowerToHeat → climate_state`
  so the (rare) condition is caught live during drives, not only at 30-min prime cadence.

Net: **no missing coverage** — every column TeslaMate persists is supplied. The only open item is the
optional live mapping for `battery_heater_no_power`.

### F3 — Gear normalization uses the weaker decoder (Low, latent)
`assemble`/`accumulate` use `strip_state` (handles `ShiftState*`), not `gear_letter` (also handles
`DriveGear*`/`"Drive"`/`"Park"`). Current telemetry emits `ShiftState*` (fixture-confirmed), so it
works today; switching to `gear_letter` would harden against format drift. If it ever failed,
`shift_state→None` would suppress **both** speed and power on that frame.

### F4 — REST has no `heading` (Info, by design)
TeslaMate's REST `create_position` has no `heading` key; `positions.heading` is filled only from the
streaming `est_heading`. The shim's `GpsHeading` in REST drive_state reaches only MQTT, not `positions`.

---

<a id="ground-truth-2026-06-19-drive"></a>
## 5. Ground truth — 2026-06-19 captured drive

A short drive (a D→R→P parking maneuver, ~0.1 mi) was captured in the persistent logs and used to
validate the findings against real telemetry:

- **F1 confirmed (then fixed).** No `Power` field ever arrived (count = 0); `PackVoltage` 359.8–363.9 V
  and `PackCurrent` −33.9→+15.5 A streamed throughout. Real battery power swung ≈ **+12 kW (drive)**
  to **−6 kW (regen)** — all of which the stream emitted as **0** before the fix.
- **Gear-on-park RESOLVED — the stream DOES emit park explicitly.** Observed `Gear` values:
  `ShiftStateP` ×7, `ShiftStateR` ×4, `ShiftStateD` ×3, `<invalid>` ×4, in a clear D→R→P sequence.
  So the stream sends the shift state on change (including `ShiftStateP`); it does **not** signal
  "parked" only by going silent. Both `ShiftStateP`→"P" and `<invalid>`→None read as parked by
  TeslaMate, so park handling is correct. (This also informs the separate single-source-of-truth
  refactor discussion: last-writer-wins on gear is viable because park is actively reported.)
- **F5 found (then fixed).** `ChargingCableType` = `"CableTypeSAE"` and a charge session followed
  (`DetailedChargeStateCharging`/`Starting` → strip_state → "Charging"/"Starting" ✓). `strip_state`
  cleaned the `...State` enums but left `CableTypeSAE` unchanged; `strip_enum` now yields `"SAE"`.
- **Still open: `VehicleSpeed` unit.** The maneuver was a parking-lot crawl (max `VehicleSpeed` = 8,
  0.1 mi), so mph-vs-km/h is still not stress-tested. Consistent with mph for this US (miles) vehicle;
  a highway drive would confirm definitively.

## 6. Recommendations

1. ~~**F1:** compute streaming `power` from `PackVoltage*PackCurrent`.~~ **Done (v1.0.16)** —
   `ws_stream.build_data_update`.
2. ~~**F5:** strip the `CableType`/`FastChargerType` enum prefixes.~~ **Done (v1.0.16)** —
   `fields.strip_enum`.
3. **F2:** nothing required — `charger_pilot_current` and `fast_charger_brand` already flow (DB-verified
   via the prime-backfill). *Optional only:* a live `NotEnoughPowerToHeat → climate_state.battery_heater_no_power`
   mapping so that rare state is caught during drives (not just at prime cadence); deferred (per-user, docs-only).
4. **F3 (deferred):** harden gear normalization (a safe `gear_letter` wrapper) — not triggered by
   observed telemetry.
5. Confirm the `VehicleSpeed` unit on a highway drive (the only remaining open item).
