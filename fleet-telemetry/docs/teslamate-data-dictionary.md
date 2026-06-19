# TeslaMate Data Dictionary (v4.0.1)

A complete reference for the data **TeslaMate** ingests, transforms, and persists: every API it
consumes, every field, the expected unit and range, and the trace from API ingress to durable
egress (PostgreSQL **and** MQTT).

> **Scope.** This documents **TeslaMate's own source code** — it is *not* an analysis of the Fleet
> Telemetry add-on. It exists because we are building that add-on to feed TeslaMate, so we need an
> authoritative description of exactly what TeslaMate expects to receive and how it stores it.

## Provenance

- **Pinned version:** TeslaMate app **`v4.0.1`** (the version installed here — confirmed from the
  running container at `/opt/app/lib/teslamate-4.0.1`; the HA add-on wrapper `lildude/ha-addon-teslamate:2.5.0`
  is unrelated to the data model).
- **Source:** `teslamate-org/teslamate` at git tag `v4.0.1`, read read-only.
- **Coverage:** complete — every field in TeslaMate's API structs (including fields it parses but
  discards), every Postgres column, and every MQTT topic.
- **To regenerate:** `git clone --depth 1 --branch v4.0.1 https://github.com/teslamate-org/teslamate`
  and re-read the files cited inline (`file:line`).

## Top-line findings (read these first)

1. **TeslaMate v4.0.1 uses the legacy *Owner API* shape, not the Fleet API.** Endpoints are
   `owner-api.teslamotors.com/api/1/...`, auth is `auth.tesla.com` with `client_id=ownerapi`. It is
   designed to be pointed at a **Fleet-API proxy** via `TESLA_API_HOST` and an appended `TOKEN` path
   segment (this is exactly the seam the Fleet Telemetry shim / MyTeslaMate proxy plug into).
   `tesla_api/vehicle.ex:27-67`, `tesla_api/auth.ex:6,21`.
2. **Two ingestion paths, different field sets.** The REST `vehicle_data` poll fills the full
   `positions`+`charges` column set; the **Streaming WebSocket** fills only a subset of `positions`
   (location, speed, power, odometer, soc, elevation) and drives the drive-state machine.
3. **`gui_settings` is requested but never parsed.** The `endpoints=` param asks for
   `gui_settings`, `closures_state`, `vehicle_data_combo`, but `result/1` has no parser for them —
   so distance/temperature display units from Tesla are **dropped entirely**. `vehicle.ex:61-96`.
4. **Streaming `range`, `est_range`, and the raw `heading` column are decoded then dropped** — never
   read by any ingress function. Battery range is therefore persisted **only via the REST path**.
   `stream/data.ex:2-3` vs. `vehicle.ex` (no `stream_data.range`/`.est_range` references).
5. **`power` and `charger_power` are stored in kW** (integer, no conversion) — the Tesla API
   delivers them in kW. `position.ex:14`, `charge.ex:18`, `vehicle.ex:1574,1632`.
6. **Elevation is not a Tesla field.** Streaming supplies it; for REST-only cars TeslaMate computes
   it from a **local SRTM/DEM** (`TeslaMate.Terrain`), not any API. `vehicle.ex:1598-1602`, `terrain.ex`.
7. **Storage is SI**: km, °C, kW, kWh, bar, meters, degrees, km/h, %. Conversions (miles→km,
   mph→km/h) happen in `vehicles/vehicle.ex` via `Convert.*`; °F/imperial helpers exist only for
   display/MQTT-out, never for storage.

---

## 1. APIs / data sources

All Tesla transports use OAuth bearer tokens. Hosts are region-aware (`auth.region/1` reads the
token issuer TLD: `.cn → :chinese`, else `:global`) and overridable by env vars.

| # | API / source | Purpose | Transport | Auth | Endpoint | Source |
|---|---|---|---|---|---|---|
| 1 | **OAuth token refresh** | refresh `access_token` | HTTPS POST | `grant_type=refresh_token`, `client_id=ownerapi`, scope `openid email offline_access` | `{auth_host}/oauth2/v3/token` (default `auth.tesla.com`) | `auth.ex:6,21,30-41`, `auth/refresh.ex:9-26` |
| 2 | **Products list** | enumerate vehicles | HTTPS GET | Bearer | `{api_host}/api/1/products` | `vehicle.ex:25-36` |
| 3 | **Vehicle (no state)** | summary/state without waking | HTTPS GET | Bearer | `{api_host}/api/1/vehicles/{id}` | `vehicle.ex:38-49` |
| 4 | **vehicle_data** | full live snapshot (§2) | HTTPS GET | Bearer | `{api_host}/api/1/vehicles/{id}/vehicle_data?endpoints=...` | `vehicle.ex:51-96` |
| 5 | **Streaming telemetry** | high-frequency drive data (§3) | WSS | Bearer in subscribe frame | `{wss_host}/streaming/` (default `streaming.vn.teslamotors.com`) | `stream.ex:33-88` |
| — | **PostgreSQL** | durable egress (§5) | Ecto/SQL | — | local DB | `lib/teslamate/log/*` |
| — | **MQTT** | live egress (§6) | MQTT QoS 1 | — | `teslamate[/{ns}]/cars/{id}/...` | `mqtt/pubsub/vehicle_subscriber.ex` |

**`vehicle_data` `endpoints=` param (verbatim, `vehicle.ex:61-63`):**
`charge_state;climate_state;closures_state;drive_state;gui_settings;location_data;vehicle_config;vehicle_state;vehicle_data_combo`
— but only `charge_state`, `climate_state`, `drive_state`, `vehicle_config`, `vehicle_state` are
parsed. `location_data` is what makes Tesla embed lat/long in `drive_state`.

**Host / proxy overrides:** `TESLA_API_HOST` (all REST calls), `TESLA_AUTH_HOST` (+`TESLA_AUTH_PATH`,
`TESLA_AUTH_CLIENT_ID`), `TESLA_WSS_HOST` (+`TESLA_WSS_TLS_ACCEPT_INVALID_CERTS`). An undocumented
`TOKEN` env var is appended to every REST/WSS path for proxying (`vehicle.ex:32,45,59`, `stream.ex:38,42`).

---

## 2. REST `vehicle_data` field inventory (ingress)

Per field: **API unit** and whether it is **persisted** (DB column and/or MQTT) or **parsed-only**
(decoded into the struct but never referenced by `vehicles/vehicle.ex` or `summary.ex`). Sub-structs
live in `lib/tesla_api/vehicle/state.ex`. Persistence determined from `vehicles/vehicle.ex`
(`create_position`/`insert_charge` → `positions`/`charges`) and `summary.ex` (→ MQTT).

**Unit baseline (verified):** ranges & odometer = **miles** (→km on store), speed = **mph** (→km/h),
temps = **°C** (stored raw), TPMS = **bar** (raw), `power`/`charger_power` = **kW** (raw),
`charger_voltage` = **V**, currents = **A**, energy = **kWh**, timestamps = **unix ms**.

### 2.1 `charge_state` (Charge — `state.ex:2-46`)

| Field | API unit | Persisted? | Notes |
|---|---|---|---|
| battery_level | % | DB positions+charges, MQTT | `vehicle.ex:1575,1626`; `summary.ex:101` |
| usable_battery_level | % | DB positions+charges, MQTT | `vehicle.ex:1576,1627` |
| charge_energy_added | kWh | DB charges, MQTT | `vehicle.ex:1628` |
| charger_power | **kW** | DB charges, MQTT | raw `||0`; `vehicle.ex:1632` |
| charger_voltage | V | DB charges, MQTT | `vehicle.ex:1633` |
| charger_actual_current | A | DB charges, MQTT | `vehicle.ex:1629` |
| charger_pilot_current | A | DB charges | `vehicle.ex:1631` |
| charger_phases | count | DB charges, MQTT | `vehicle.ex:1630` |
| ideal_battery_range | miles | DB positions+charges (→km), MQTT | `vehicle.ex:1580,1638` |
| est_battery_range | miles | DB positions (→km), MQTT | `vehicle.ex:1581` |
| battery_range (="rated") | miles | DB positions+charges → `rated_battery_range_km`, MQTT | `vehicle.ex:1582,1639` |
| conn_charge_cable | — | DB charges | `vehicle.ex:1634` |
| fast_charger_present | — | DB charges | `vehicle.ex:1635` |
| fast_charger_brand | — | DB charges | `vehicle.ex:1636` |
| fast_charger_type | — | DB charges | `vehicle.ex:1637` |
| not_enough_power_to_heat | — | DB charges | `vehicle.ex:1640` |
| charging_state | enum | MQTT; drives FSM | `summary.ex:102`; values Charging/Starting/Complete/Disconnected |
| charge_limit_soc | % | MQTT | `summary.ex:106` |
| charge_current_request / _max | A | MQTT | `summary.ex:103-104` |
| charge_port_door_open | — | MQTT | `summary.ex:107` |
| scheduled_charging_start_time | unix s | MQTT (→DateTime) | `summary.ex:116` |
| time_to_full_charge | hours | MQTT | `summary.ex:118` |
| timestamp | unix ms | used (charge date) | `vehicle.ex:1622` |
| charge_miles_added_ideal / _rated | miles | parsed-only | |
| charge_rate | mi/hr | parsed-only | |
| charge_limit_soc_{min,max,std} | % | parsed-only | |
| charge_port_latch | — | parsed-only | |
| charge_port_cold_weather_mode | — | parsed-only | |
| charge_to_max_range, max_range_charge_counter | — | parsed-only | |
| managed_charging_active / _start_time / _user_canceled | — | parsed-only | |
| scheduled_charging_pending | — | parsed-only | |
| trip_charging | — | parsed-only | |
| charge_enable_request, user_charge_enable_request | — | parsed-only | |
| battery_heater_on | — | DB positions+charges | `vehicle.ex:1589,1623` |

### 2.2 `climate_state` (Climate — `state.ex:96-129`)

| Field | API unit | Persisted? | Notes |
|---|---|---|---|
| outside_temp | °C | DB positions+charges, MQTT | `vehicle.ex:1577,1641` |
| inside_temp | °C | DB positions, MQTT | `vehicle.ex:1578` |
| driver_temp_setting | °C | DB positions | `vehicle.ex:1585` |
| passenger_temp_setting | °C | DB positions | `vehicle.ex:1586` |
| fan_status | 0–7 | DB positions | `vehicle.ex:1583` |
| is_climate_on | — | DB positions, MQTT | `vehicle.ex:1584` |
| is_front_defroster_on | — | DB positions | `vehicle.ex:1588` |
| is_rear_defroster_on | — | DB positions | `vehicle.ex:1587` |
| is_preconditioning | — | MQTT | `summary.ex:123` |
| climate_keeper_mode | enum | MQTT | `summary.ex:124` |
| battery_heater | — | DB positions+charges | `vehicle.ex:1590,1624` |
| battery_heater_no_power | — | DB positions+charges | `vehicle.ex:1591,1625` |
| seat_heater_* (7), steering_wheel_heater, side_mirror_heaters, wiper_blade_heater | 0–3 / — | parsed-only | |
| defrost_mode, smart_preconditioning, remote_heater_control_enabled | — | parsed-only | |
| is_auto_conditioning_on, max_avail_temp, min_avail_temp | °C/— | parsed-only | |
| left_temp_direction, right_temp_direction, timestamp | — | parsed-only | |

### 2.3 `drive_state` (Drive — `state.ex:168-189`)

| Field | API unit | Persisted? | Notes |
|---|---|---|---|
| latitude / longitude | ° | DB positions, MQTT | `vehicle.ex:1571-1572` |
| heading | ° | DB positions, MQTT | (stream `est_heading` feeds this) `vehicle.ex:1841` |
| speed | mph | DB positions (→km/h), MQTT (→km/h) | nullable when parked; `vehicle.ex:1573` |
| power | **kW** | DB positions, MQTT | raw int; `vehicle.ex:1574` |
| shift_state | P/R/N/D (nullable) | DB positions, MQTT; drives FSM | `summary.ex:97` |
| active_route_destination | — | MQTT | `summary.ex:82` |
| active_route_latitude / _longitude | ° | MQTT | `summary.ex:83-84` |
| active_route_miles_to_arrival | **miles (not converted)** | MQTT (`active_route` JSON) | `summary.ex:87` |
| active_route_minutes_to_arrival | minutes | MQTT | `summary.ex:89` |
| active_route_traffic_minutes_delay | minutes | MQTT | `summary.ex:91` |
| active_route_energy_at_arrival | % | MQTT | `summary.ex:85` |
| timestamp | unix ms | used (position date, stale-fetch guard) | `vehicle.ex:1570,323` |
| gps_as_of | unix s | parsed-only | |
| native_latitude/_longitude/_location_supported/_type | — | parsed-only | |

### 2.4 `vehicle_state` (VehicleState — `state.ex:276-335`)

| Field | API unit | Persisted? | Notes |
|---|---|---|---|
| odometer | miles | DB positions (→km), MQTT (→km) | `vehicle.ex:1579` |
| car_version | — | → `updates`/car version, MQTT (`version`) | `vehicle.ex:1364,1872` |
| locked | — | MQTT | `summary.ex:130` |
| sentry_mode | — | MQTT; blocks sleep | `summary.ex:131`, `vehicle.ex:1759` |
| is_user_present | — | MQTT; blocks sleep | `summary.ex:140`, `vehicle.ex:1750` |
| df, dr, pf, pr | 0/1 door | MQTT (per-door + `doors_open`) | `summary.ex:134-137` |
| ft (frunk), rt (trunk) | 0/1 | MQTT (`frunk_open`/`trunk_open`) | `summary.ex:138-139` |
| fd_window, fp_window, rd_window, rp_window | 0/1 | MQTT (`windows_open`) | `summary.ex:132,172` |
| tpms_pressure_fl/fr/rl/rr | bar | DB positions, MQTT | `vehicle.ex:1592-1595` |
| tpms_soft_warning_fl/fr/rl/rr | — | MQTT | `summary.ex:148-151` |
| center_display_state | enum | MQTT | `summary.ex:152` |
| software_update.status | enum | MQTT (`update_available`); FSM | `vehicle.ex:1018,1350` |
| software_update.version | — | MQTT (`update_version`); `updates` | `vehicle.ex:1354` |
| software_update.install_perc | % | used (update guard) | `vehicle.ex:1366` |
| software_update.download_perc / expected_duration_sec / scheduled_time_ms | %/s/ms | parsed-only | |
| vehicle_name | — | used as display_name (top-level) | `vehicle.ex:84` |
| api_version, calendar/notifications/homelink*, autopark*, valet*, remote_start*, summon*, sun_roof_* | — | parsed-only | |
| timestamp | unix ms | used | |

### 2.5 `vehicle_config` (VehicleConfig — `state.ex:216-243`)

Feeds the **`cars`** table at car create/update; only a few reach MQTT via the persisted `Car`
record (`model`, `trim_badging`, `exterior_color`, `wheel_type`, `spoiler_type` — `summary.ex:30-33,69-73`).
All others (`car_type`, `charge_port_type`, `rhd`, `eu_vehicle`, `has_air_suspension`,
`has_ludicrous_mode`, `rear_seat_*`, `roof_color`, `sun_roof_installed`, `third_row_seats`,
`can_*`, `motorized_charge_port`, `plg`, `key_version`, `perf_config`, `car_special_type`,
`use_range_badging`, `seat_type`, `timestamp`) are **parsed-only**.

### 2.6 `gui_settings` — NOT IMPLEMENTED

Requested in `endpoints=` but there is no `GuiSettings` sub-struct and `result/1` never parses it
(`vehicle.ex:75-96`); the top-level `gui_settings` is always `nil`. So `gui_distance_units`,
`gui_temperature_units`, `gui_charge_rate_units`, `gui_24_hour_time`, `gui_range_display`,
`show_range_units` are **dropped entirely**. `closures_state`, `vehicle_data_combo` likewise unparsed.

---

## 3. Streaming API (ingress)

WebSocket (`WebSockex`) to `{wss_host}/streaming/` (`stream.ex:33-54`). Subscribe frame
`msg_type: "data:subscribe_oauth"` with the comma-joined column list and `tag: "{vehicle_id}"`
(`stream.ex:81-88`). On `data:update`, the CSV is zipped against `[:time | @columns]` and parsed by
`Data.into!` (`stream.ex:123-132`, `stream/data.ex`).

| Field | Order | API unit | Parsed | Converted to | DB column |
|---|---|---|---|---|---|
| time | 0 (prepended) | unix ms | DateTime | unix | `positions.date` |
| speed | 1 | mph | int | `mph_to_kmh` | `positions.speed` (km/h) |
| odometer | 2 | miles | float | `miles_to_km(_,6)` | `positions.odometer` (km) |
| soc | 3 | % | int | raw | `positions.battery_level` |
| elevation | 4 | meters | int | raw | `positions.elevation` (m) |
| est_heading | 5 | ° | int | → in-memory `drive_state.heading` | (via merge; not by create_position) |
| est_lat | 6 | ° | float | raw | `positions.latitude` |
| est_lng | 7 | ° | float | raw | `positions.longitude` |
| power | 8 | **kW** | int | raw (no /1000) | `positions.power` (kW) |
| shift_state | 9 | P/R/N/D | string ("" → nil) | raw; gates drive FSM | none directly |
| range | 10 | miles | int | **dropped** | **none** |
| est_range | 11 | miles | int | **dropped** | **none** |
| heading | 12 | ° | int | **dropped** (merge uses `est_heading`) | **none** |

> The streaming path does **not** persist battery ranges, temperatures, or usable SoC — those come
> only from the REST poll.

---

## 4. Transform / unit layer (`lib/teslamate/convert.ex`)

Constants: `@km_factor = 0.62137119223733` (mi/km), `@ft_factor = 3.28084` (ft/m). Each function has
`nil`-passthrough, `%Decimal{}`, and plain-number clauses; `precision == 0` returns a rounded integer.

| Function | Formula | In → Out | Used for |
|---|---|---|---|
| `mph_to_kmh/1` | `mph / 0.62137…` (→int) | mph → km/h | storage (speed) |
| `miles_to_km/2` | `mi / 0.62137…` | miles → km | storage (odometer p=6, ranges p=2) |
| `km_to_miles/2` | `km * 0.62137…` | km → miles | display/MQTT-out only |
| `m_to_ft/1` | `m * 3.28084` | meters → feet | display only |
| `ft_to_m/1` | `ft / 3.28084` | feet → meters | display only |
| `celsius_to_fahrenheit/2` | `c*9/5+32` | °C → °F | display only |

No °F/imperial conversion is applied on ingest — temps stored °C, pressures stored bar.

---

## 5. PostgreSQL data dictionary (egress)

Storage conventions: distances/ranges **km**, temps **°C**, power **kW**, energy **kWh**, SoC **%**,
pressure **bar**, elevation **m**, lat/long/heading **degrees**, speed **km/h**, timestamps **UTC**.
`numeric(p,s)` decimal columns are `read_after_writes` in Ecto (DB rounds to scale). The
`20200410112005_database_efficiency_improvements` migration narrowed many `float`/`integer` columns
to `smallint`/`numeric` — note the precision loss on `power`/`speed` (integer kW / km/h).

### 5.1 `positions` (highest-frequency; `log/position.ex:7-39`, create `20190330170000`)
One row per awake sample; `drive_id` set while driving, else NULL.

| Column | SQL type | Unit | Range | Null/default | Notes |
|---|---|---|---|---|---|
| id | integer PK | — | — | NOT NULL | |
| date | timestamp(6) | UTC | — | NOT NULL | |
| latitude | numeric(8,6) | ° | −90..90 | NOT NULL | |
| longitude | numeric(9,6) | ° | −180..180 | NOT NULL | |
| elevation | smallint | m | −32768..32767 | NULL | renamed from `altitude` |
| speed | smallint | km/h | ≥0 | NULL | |
| power | smallint | **kW** | signed (− = regen) | NULL | sub-kW lost |
| odometer | double precision | km | ≥0 | NULL | |
| ideal_battery_range_km | numeric(6,2) | km | ≥0 | NULL | |
| est_battery_range_km | numeric(6,2) | km | ≥0 | NULL | |
| rated_battery_range_km | numeric(6,2) | km | ≥0 | NULL | |
| battery_level | smallint | % | 0..100 | NULL | |
| usable_battery_level | smallint | % | 0..100 | NULL | |
| battery_heater / _on / _no_power | boolean | — | t/f | NULL | |
| outside_temp / inside_temp | numeric(4,1) | °C | — | NULL | |
| fan_status | integer | level | 0–7 | NULL | |
| driver_temp_setting / passenger_temp_setting | numeric(4,1) | °C | — | NULL | |
| is_climate_on / is_rear_defroster_on / is_front_defroster_on | boolean | — | t/f | NULL | |
| tpms_pressure_fl/fr/rl/rr | numeric(4,1) | bar | ≥0 | NULL | |
| car_id | smallint | FK→cars | — | NOT NULL | |
| drive_id | integer | FK→drives | — | NULL | NULL when not driving |

### 5.2 `charges` (`log/charge.ex:7-30`, create `20190330200000`)
One row per sample while charging; child of `charging_processes`.

| Column | SQL type | Unit | Range | Null/default | Notes |
|---|---|---|---|---|---|
| id | integer PK | — | — | NOT NULL | |
| date | timestamp(6) | UTC | — | NOT NULL | |
| battery_level / usable_battery_level | smallint | % | 0..100 | NULL | |
| charge_energy_added | numeric(8,2) | kWh | ≥0 | NOT NULL | |
| charger_actual_current / charger_pilot_current | smallint | A | ≥0 | NULL | |
| charger_phases | smallint | count | >0 | NULL (Ecto default 1, app-only) | |
| charger_power | smallint | **kW** | ≥0 | NOT NULL | |
| charger_voltage | smallint | V | ≥0 | NULL | |
| conn_charge_cable | varchar(255) | — | — | NULL | |
| fast_charger_present | boolean | — | t/f | NULL | |
| fast_charger_brand / fast_charger_type | varchar(255) | — | — | NULL | |
| ideal_battery_range_km | numeric(6,2) | km | ≥0 | NOT NULL | |
| rated_battery_range_km | numeric(6,2) | km | ≥0 | NULL | |
| not_enough_power_to_heat | boolean | — | t/f | NULL | |
| battery_heater / _on / _no_power | boolean | — | t/f | NULL | |
| outside_temp | numeric(4,1) | °C | — | NULL | |
| charging_process_id | integer | FK→charging_processes | — | NOT NULL | |

(No `est_battery_range_km`, no `inside_temp` on `charges`.)

### 5.3 `charging_processes` (`log/charging_process.ex:8-29`, create `20190330190000`)
One row per plug→unplug session, aggregating its `charges`.

| Column | SQL type | Unit | Null/default | Notes |
|---|---|---|---|---|
| id | integer PK | — | NOT NULL | |
| start_date / end_date | timestamp(6) | UTC | start NOT NULL / end NULL | |
| charge_energy_added / charge_energy_used | numeric(8,2) | kWh | NULL | `used` ≠ `added` if solar/battery |
| start_/end_ideal_range_km | numeric(6,2) | km | NULL | |
| start_/end_rated_range_km | numeric(6,2) | km | NULL | |
| start_/end_battery_level | smallint | % | NULL | |
| duration_min | smallint | minutes | NULL | |
| outside_temp_avg | numeric(4,1) | °C | NULL | |
| cost | numeric(6,2) | user currency | NULL | from geofence tariff |
| car_id | smallint | FK→cars | NOT NULL | |
| position_id | integer | FK→positions | NOT NULL | plug-in position |
| address_id | integer | FK→addresses | NULL | |
| geofence_id | integer | FK→geofences | NULL | |

### 5.4 `drives` (`log/drive.ex:8-39`, create `20190330160000` → renamed `20190812191616`)
One row per drive, aggregating its `positions`.

| Column | SQL type | Unit | Null | Notes |
|---|---|---|---|---|
| id | integer PK | — | NOT NULL | |
| start_date / end_date | timestamp(6) | UTC | start NOT NULL | |
| outside_temp_avg / inside_temp_avg | numeric(4,1) | °C | NULL | |
| speed_max | smallint | km/h | NULL | |
| power_max / power_min | smallint | kW | NULL | min = peak regen |
| start_/end_ideal_range_km | numeric(6,2) | km | NULL | |
| start_/end_rated_range_km | numeric(6,2) | km | NULL | |
| start_km / end_km / distance | double precision | km | NULL | |
| duration_min | smallint | minutes | NULL | |
| ascent / descent | smallint | m | NULL | added `20250613133700` (from elevation deltas) |
| start_/end_position_id | integer | FK→positions | NULL | |
| start_/end_address_id | integer | FK→addresses | NULL | |
| start_/end_geofence_id | integer | FK→geofences | NULL | |
| car_id | smallint | FK→cars | NOT NULL | |

### 5.5 `cars` (`log/car.ex:8-31`)

| Column | SQL type | Unit | Null/default | Notes |
|---|---|---|---|---|
| id | smallint PK | — | NOT NULL | |
| eid / vid | bigint | — | NOT NULL | UNIQUE (Tesla ids) |
| vin | text | — | NULL (changeset requires) | UNIQUE |
| name | text | — | NULL | |
| model | varchar(255) | — | NULL | "S"/"3"/"X"/"Y" |
| trim_badging | text | — | NULL | renamed from `version` |
| marketing_name | varchar(255) | — | NULL | |
| exterior_color / spoiler_type / wheel_type | text | — | NULL | |
| efficiency | double precision | Wh/km factor | NULL | |
| display_priority | smallint | — | NOT NULL default 1 | |
| settings_id | bigint FK→car_settings | — | NOT NULL | UNIQUE 1:1, ON DELETE CASCADE |
| inserted_at / updated_at | timestamp | — | NOT NULL | |

### 5.6 `car_settings` (`settings/car_settings.ex:7-17`)

| Column | SQL type | Unit | Null/default | Notes |
|---|---|---|---|---|
| id | bigint PK | — | NOT NULL | |
| suspend_min | integer | minutes | NOT NULL default 21 | |
| suspend_after_idle_min | integer | minutes | NOT NULL default 15 | |
| req_not_unlocked | boolean | — | NOT NULL default false | |
| free_supercharging | boolean | — | NOT NULL default false | |
| use_streaming_api | boolean | — | NOT NULL default true | REST-vs-streaming switch |
| enabled | boolean | — | NOT NULL default true | |
| lfp_battery | boolean | — | NOT NULL default false | |

### 5.7 `states` (`log/state.ex:7-14`)

| Column | SQL type | Domain | Null | Notes |
|---|---|---|---|---|
| id | integer PK | — | NOT NULL | |
| state | enum `states_status` | **online / offline / asleep** | NOT NULL | |
| start_date / end_date | timestamp(6) | UTC | start NOT NULL / end NULL | end NULL = current |
| car_id | smallint FK→cars | — | NOT NULL | ON DELETE CASCADE |

Constraints: partial UNIQUE `(car_id) WHERE end_date IS NULL` (one open state); CHECK `end_date >= start_date`.

### 5.8 `updates` (`log/update.ex:7-13`)

| Column | SQL type | Null | Notes |
|---|---|---|---|
| id | integer PK | NOT NULL | |
| start_date / end_date | timestamp(6) | start NOT NULL | end NULL = in progress |
| version | varchar(255) | NULL | firmware string |
| car_id | smallint FK→cars | NOT NULL | ON DELETE CASCADE; CHECK end≥start |

### 5.9 `addresses` (`locations/address.ex:5-24`)

| Column | SQL type | Unit | Null | Notes |
|---|---|---|---|---|
| id | integer PK | — | NOT NULL | |
| display_name | varchar(512) | — | NULL (req) | |
| latitude | numeric(8,6) | ° | NULL (req) | |
| longitude | numeric(9,6) | ° | NULL (req) | |
| name/house_number/road/neighbourhood/city/county/postcode/state/state_district/country | varchar(255) | — | NULL | |
| osm_id | bigint | — | NULL (req) | UNIQUE with osm_type |
| osm_type | text | — | NULL (req) | node/way/relation |
| raw | jsonb | — | NULL (req) | full geocoder JSON |
| geofence_id | integer FK→geofences | — | NULL | ON DELETE NILIFY |
| inserted_at / updated_at | timestamp | — | NOT NULL | |

### 5.10 `geofences` (`locations/geo_fence.ex:6-17`)

| Column | SQL type | Unit | Null/default | Notes |
|---|---|---|---|---|
| id | integer PK | — | NOT NULL | |
| name | varchar(255) | — | NOT NULL | |
| latitude | numeric(8,6) | ° | NOT NULL | |
| longitude | numeric(9,6) | ° | NOT NULL | |
| radius | smallint | m | NOT NULL default 25 | 0 < r < 5000 |
| cost_per_unit | numeric(6,4) | currency/unit | NULL | per kWh or per min |
| session_fee | numeric(6,2) | currency | NULL | |
| billing_type | enum `billing_type` | **per_kwh / per_minute** | NOT NULL default per_kwh | |
| inserted_at / updated_at | timestamp | — | NOT NULL | |

**Geofence ↔ address (definitive):** the FK lives on **`addresses.geofence_id → geofences.id`**
(one geofence → many addresses). The original `geofences.address_id` was **removed** in
`20190925182253`. There is no address association on the `GeoFence` schema.

### 5.11 `settings` (global singleton; `settings/global_settings.ex:5-19`)

| Column | SQL type | Domain | Null/default |
|---|---|---|---|
| id | bigint PK | — | NOT NULL (one row) |
| unit_of_length | enum `length` | km / mi | NOT NULL default km |
| unit_of_temperature | enum `temperature` | C / F | NOT NULL default C |
| unit_of_pressure | enum `unit_of_pressure` | bar / psi | NOT NULL default bar |
| preferred_range | enum `range` | ideal / rated | NOT NULL default rated |
| theme_mode | text (Ecto.Enum) | light / system / dark | NOT NULL default system |
| language | text | 40+ locales | NOT NULL default en |
| base_url / grafana_url | varchar(255) | URL | NULL |
| inserted_at / updated_at | timestamp | — | NOT NULL |

**Postgres ENUM types:** `states_status(online,offline,asleep)`, `billing_type(per_kwh,per_minute)`,
`length(km,mi)`, `temperature(C,F)`, `unit_of_pressure(bar,psi)`, `range(ideal,rated)`. `theme_mode`
is plain text (enum enforced only in the app).

> **Display vs. storage:** `unit_of_length`/`_temperature`/`_pressure`/`preferred_range` affect only
> the UI/Grafana/MQTT-out rendering. Stored values are always SI.

---

## 6. MQTT egress

Topic: **`teslamate[/{namespace}]/cars/{car_id}/<field>`** (`vehicle_subscriber.ex:201-205`), where
`{car_id}` is the **internal DB id**, not VIN. Namespace from `MQTT_NAMESPACE`. **QoS 1**;
**all fields retained except `healthy`** (`@do_not_retain`, line 25). Values change-gated; `nil`
suppressed except for the `@publish_if_nil` set; `:unknown` always dropped; `DateTime`→ISO8601.

Published topics (units are post-conversion):

| Topic suffix | Type | Unit | Source |
|---|---|---|---|
| display_name | string | — | `vehicle.display_name` |
| state | string | — | derived FSM (`online/offline/asleep/suspended/driving/charging/updating/unavailable`) |
| since | datetime | — | derived (last state transition) |
| healthy | bool | — | derived (process liveness); **not retained** |
| latitude / longitude | float | ° | drive_state |
| location | JSON | ° | derived from lat+long |
| heading | int | ° | drive_state |
| speed | int | km/h | drive_state.speed → `mph_to_kmh` |
| power | int | kW | drive_state.power |
| shift_state | string | — | drive_state (publish-if-nil) |
| odometer | float | km | vehicle_state.odometer → `miles_to_km` |
| elevation | int | m | derived (stream/SRTM) |
| battery_level / usable_battery_level | int | % | charge_state |
| charge_limit_soc | int | % | charge_state |
| charging_state | string | — | charge_state |
| charge_energy_added | float | kWh | charge_state (publish-if-nil) |
| charge_current_request / _max | int | A | charge_state |
| charger_actual_current / charger_phases / charger_power / charger_voltage | int | A/—/kW/V | charge_state (publish-if-nil) |
| charge_port_door_open | bool | — | charge_state |
| plugged_in | bool | — | derived (`charging_state != "Disconnected"`) |
| scheduled_charging_start_time | datetime | — | charge_state (unix→DateTime) |
| time_to_full_charge | float | hours | charge_state |
| est_/ideal_/rated_battery_range_km | float | km | charge_state → `miles_to_km` |
| outside_temp / inside_temp | float | °C | climate_state |
| is_climate_on / is_preconditioning | bool | — | climate_state |
| climate_keeper_mode | string | — | climate_state |
| locked / sentry_mode / is_user_present | bool | — | vehicle_state |
| windows_open / doors_open / {driver,passenger}_{front,rear}_door_open / trunk_open / frunk_open | bool | — | derived (window/door/trunk > 0) |
| version / update_version | string | — | derived (first token of car_version / sw version) |
| update_available | bool | — | derived (software_update.status) |
| tpms_pressure_fl/fr/rl/rr | float | bar | vehicle_state |
| tpms_soft_warning_fl/fr/rl/rr | bool | — | vehicle_state |
| center_display_state | int | — | vehicle_state |
| model / trim_badging / exterior_color / wheel_type / spoiler_type | string | — | persisted `Car` record (not live API) |
| geofence | string | — | derived (matched geofence name / `default_geofence`) |
| active_route_destination / _latitude / _longitude | string/float | ° | drive_state (`"nil"` string when no route) |
| active_route | JSON | mixed | derived composite (incl. `miles_to_arrival` **in miles**) |

Not published as scalar topics: `car` (internal struct); `active_route_{energy_at_arrival,
miles_to_arrival,minutes_to_arrival,traffic_minutes_delay}` (only inside the `active_route` JSON).

---

## 7. End-to-end traces (high-value fields)

| Field | REST source | Stream source | Conversion | Postgres | MQTT |
|---|---|---|---|---|---|
| location | drive_state.lat/long | est_lat/est_lng | none | positions.latitude/longitude | latitude/longitude, location |
| speed | drive_state.speed (mph) | speed (mph) | `mph_to_kmh` | positions.speed (km/h) | speed |
| power | drive_state.power (kW) | power (kW) | none | positions.power (kW) | power |
| odometer | vehicle_state.odometer (mi) | odometer (mi) | `miles_to_km(_,6)` | positions.odometer (km) | odometer |
| soc | charge_state.battery_level | soc | none | positions/charges.battery_level | battery_level |
| ideal range | charge_state.ideal_battery_range (mi) | — | `miles_to_km(_,2)` | *_ideal_range_km | ideal_battery_range_km |
| est range | charge_state.est_battery_range (mi) | — (dropped) | `miles_to_km(_,2)` | positions.est_battery_range_km | est_battery_range_km |
| rated range | charge_state.battery_range (mi) | — (dropped) | `miles_to_km(_,2)` | *_rated_range_km | rated_battery_range_km |
| charge energy | charge_state.charge_energy_added (kWh) | — | none | charges.charge_energy_added | charge_energy_added |
| temps | climate_state.outside/inside_temp (°C) | — | none | positions/charges.*temp | outside/inside_temp |
| elevation | **SRTM lookup** (REST-only) | elevation (m) | none | positions.elevation (m) | elevation |

---

## 8. Caveats / open questions

- **Owner-API vs Fleet-API:** v4.0.1 emits Owner-API-shaped requests; production here routes them
  through a Fleet proxy via `TESLA_API_HOST`/`TOKEN`. Field *shapes* are identical; only host/auth differ.
- **Dropped data:** `gui_settings`/`closures_state`/`vehicle_data_combo` (never parsed); streaming
  `range`/`est_range`/raw `heading` (decoded, never stored).
- **Precision loss:** `power`/`charger_power`/`speed` stored as integer kW / km/h (sub-unit lost via
  the `smallint` migration).
- **`cost` unit** = the operator's configured currency (not a fixed unit).
- **`active_route_miles_to_arrival`** is the one published value left in **miles** (not converted).
- All `file:line` references are against tag `v4.0.1`; re-verify if the installed version changes.
