# Fleet Telemetry → TeslaMate API Conformance Audit

Does the Fleet Telemetry add-on supply TeslaMate everything it persists, in the unit/format/shape
TeslaMate expects — **and does its emulation of Tesla's REST + streaming APIs drive TeslaMate's
state machine correctly?** This checks the add-on's two TeslaMate-facing surfaces against
[`teslamate-data-dictionary.md`](./teslamate-data-dictionary.md) and against the **TeslaMate v4.0.1
source** (cloned and read for this audit).

- **Add-on version reviewed:** v1.0.23 (current `main`).
- **Architecture note (changed since the v1.0.16 review):** the add-on is now ONE unified async app
  built on a per-VIN **Store** (`app/state.py`) that is the single source of truth. Two writers feed
  it — the live telemetry stream (continuous) and a one-time Fleet-API **seed** (+ a charge-start
  refresh of the two non-streamed charge fields). All three sinks read the same snapshot:
  - **REST shim** — `app/sinks/shim_rest.py` + `app/sinks/shim_data.py` (assembles `vehicle_data`).
  - **Streaming WS** — `app/sinks/stream.py` (the `StreamSink` that consumes the Store bus) driving
    the pure builders in `ws_stream.py`. Note the prior review's `ws_stream`-tails-the-file path is
    now the *fallback*; the live path is `StreamSink`.
  - **v1.0.22 park-disconnect:** the stream sink now emits `data:update` **only while driving**, and
    on the drive→park edge sends a final frame + `data:error vehicle_disconnected`, then goes quiet.
- **Evidence basis:** add-on code (`file:line`); the dictionary; the **TeslaMate v4.0.1 clone**
  (`lib/tesla_api/*`, `lib/teslamate/vehicles/vehicle.ex`); the live `teslamate` Postgres; and
  `/data/telemetry-log.jsonl` from the running add-on.
- **Guiding principle:** TeslaMate expects **Tesla-native imperial input** (speed=mph,
  ranges/odometer=miles, temps=°C, power/charger_power=kW, pressure=bar, energy=kWh) and does its own
  SI conversion. The add-on must emit native units, never pre-converted SI.

## Executive summary

**Overall: conformant, and the v1.0.22 stream rewrite is consistent with how TeslaMate actually
closes drives.** Units, formats, enum strings, JSON/CSV shapes and the 13-column stream order all
match (re-verified). No missing coverage. The recent stream-sink rewrite was audited against
TeslaMate's own FSM and found sound, with one **low-severity robustness item** around the `Gear`
`<invalid>` sentinel — which the user flagged and which this audit confirms is safe to remove.

| Sev | Finding | Surface | Status |
|---|---|---|---|
| **Low (robustness)** | G1 — Store writes the `<invalid>` Gear sentinel (clobbers last gear); redundant because park is always signalled by an explicit `ShiftStateP` first | Store→stream/REST | **fixed v1.0.24** (persist last gear; `<invalid>` skipped for all fields) |
| **Low** | SL1 — stream monitor latched the first non-online `/products` state and never re-polled → stale `offline` stuck for hours while the car was asleep | REST state | **fixed v1.0.24** (`/products` re-confirm on `FT_SLEEP_RECHECK_SECS` cadence; no-wake) |
| **Low** | SP1 — REST `drive_state.speed` was `null` when parked; TeslaMate skips a nil `speed` on its retained MQTT topic (`speed` ∉ `@publish_if_nil`) → `sensor.tesla_speed` stuck at the last driving value | REST→MQTT | **fixed v1.0.25** (report `speed=0` when parked so TeslaMate publishes 0 and clears) |
| Info | G2 — every transition to `P` (incl. brief P during parking maneuvers) fires `vehicle_disconnected` | Streaming | by design (v1.0.22); TeslaMate-tolerant — see §3 |
| Low | S1 — streaming frame timestamp is now Store **receive-time** (`event["at"]`, whole-second), not telemetry `CreatedAt` | Streaming | verify intended |
| Low | U1 — `vehicle_state.software_update` hard-coded empty; streamed/seeded `SoftwareUpdate*` ignored | REST | `updates` table never populates (cosmetic) |
| Info | W1 — no `/wake_up` route (404 if TeslaMate ever wakes) | REST | by design ("never wake the car") |
| Info (latent) | L1 — gear normalised with `strip_state`, not `gear_letter` | REST+stream | carried over; not triggered by observed telemetry |
| Info | L2 — `is_user_present` hard-coded `False` | REST | harmless |

**Re-verified still-correct (was fixed/validated in v1.0.16; confirmed unchanged + DB-checked here):**

- **Streaming `power` (kW, ±) — HOLDS post-refactor.** `ws_stream.build_data_update` computes
  `-PackVoltage*PackCurrent/1000` (ws_stream.py:82-85). Live DB: drive **1164** (2026-06-20) =
  **538/545** positions non-zero power, range **−37 → +74 kW** (regen captured); drive **1163** =
  **1216/1222** (99.5%). (Contrast pre-fix drive 1160 = 36/1132.)
- **Enum prefix stripping** (`CableTypeSAE`→`SAE`, `…State*`→bare) — `fields.strip_enum` intact
  (fields.py:59-67), used at shim_data.py:52,54,59.
- **13/13 streaming CSV column order** — ws_stream.py:92-106 vs dict §3. Exact match.
- **Native imperial units end-to-end** — no SI double-conversion (REST `assemble` + stream builder
  pass `VehicleSpeed`/`Odometer`/ranges through `F.num`, no `Convert.*`).
- **The two non-streamed charge fields — DB re-checked.** Mechanism changed (was per-call
  prime-backfill; now seed + charge-start `update_charge_fields`, read directly at shim_data.py:58-59).
  Live DB `charges`: total **71,815**; `charger_pilot_current` **71,815 (100%)**;
  `fast_charger_brand` **71,101 (98.9%)** — non-null when the Fleet API supplies a real DC brand.

---

## 1. REST `vehicle_data` conformance (`shim_data.assemble`)

Every TeslaMate-persisted REST field, in native imperial. Unchanged in shape from the v1.0.16 review;
the column→source map below was re-checked against current `shim_data.py`.

### drive_state → positions
| TeslaMate col | unit | source | verdict | evidence |
|---|---|---|---|---|
| latitude/longitude | ° | `Location`→`parse_location` | OK | shim_data.py:24 |
| speed | mph | `VehicleSpeed` (driving-gated) | OK | shim_data.py:32 |
| power | kW (±) | `-PackVoltage*PackCurrent/1000` (driving), else 0 | OK | shim_data.py:28-29 |
| shift_state | P/R/N/D\|null | `Gear`→`strip_state`; only D/R/N kept, else null | OK (latent L1) | shim_data.py:25-26 |
| odometer | miles | `Odometer` | OK | shim_data.py:81 |

### charge_state → charges/positions
All present and native (battery levels, ranges, `charge_energy_added` via session baseline,
`charger_power` = AC+DC kW, voltage/current/phases, cable/charger enums, pilot current + brand from
seed, charging-state FSM with `None→"Disconnected"`). See shim_data.py:43-64,109-110. No regressions.

### climate_state / vehicle_state → positions
Temps °C, fan 0–7, defroster/AC bools, TPMS bar, doors/windows. Present. **Exception U1:**
`software_update` is hard-coded to `{status:"", download_perc:0, install_perc:0, version:""}`
(shim_data.py:91) even though `fields.fleet_api_to_fields` seeds `SoftwareUpdateVersion`/percent
(fields.py:213-215). So TeslaMate's `updates` table never fills. Cosmetic, but a real dead path.

---

## 2. Streaming `data:update` conformance (`ws_stream.build_data_update`)

CSV column order vs TeslaMate's `[:time | @columns]` (dict §3, parsed by `Stream.Data.into!` in
`tesla_api/stream/data.ex`). **13/13 exact match**; power computed (kW), range/est_range/heading still
populated but decoded-then-dropped by TeslaMate (harmless). One change worth flagging:

- **S1 — timestamp source changed.** The live `StreamSink` builds the frame with `event["at"]`
  (stream.py:106,121), which is the Store's wall-clock **receive time** (`now=time.time()`,
  state.py:149); `_epoch_ms` then truncates to whole seconds (ws_stream.py:26-33). The prior review
  (and the standalone `ws_stream.Stream.feed`) used the telemetry **`CreatedAt`**. Net: `positions.date`
  on stream-fed rows is receive-time, second-resolution — likely fine (sub-second drive sampling is
  unaffected), but it's a divergence from the documented behavior; confirm it's intended.

---

## 3. Drive lifecycle — does the v1.0.22 stream rewrite drive TeslaMate correctly?

This is the substantive new section: the v1.0.22 sink stopped streaming while parked and started
sending `vehicle_disconnected` on the park edge. I read TeslaMate v4.0.1 to confirm the contract.

### 3.1 TeslaMate never closes a drive from a stream frame

In the `{:driving, …}` state, the stream handler **only** acts on `D/N/R` (insert a position);
**any other shift_state (`"P"`/nil) falls through to `schedule_fetch(0)`** — an *immediate REST poll*,
not a drive close (`vehicles/vehicle.ex:653-672`). Drive closing happens exclusively in the **REST**
`{:driving, :available}` path: a `vehicle_data` poll whose `drive_state.shift_state ∈ [nil, "P"]`
calls `close_drive` (`vehicle.ex:1303-1320`). So **the stream is position-fill only; REST is the
source of truth for ending a drive.**

The poll cadence while driving is the key (`vehicle.ex:1287`):
`interval = if streaming?(data), do: default_interval() (15 s), else: driving_interval() (2.5 s)`,
and `streaming?` is simply "stream process alive" (`vehicle.ex:1916`). **This is exactly why v1.0.22
matters:** while the stream looks connected TeslaMate polls REST slowly (15 s); the park frame's
`schedule_fetch(0)` forces one immediate poll, and the `vehicle_disconnected` + going-quiet keeps the
close prompt from being lost. The mechanism is sound and matches the changelog rationale.

### 3.2 `vehicle_disconnected` is benign to drive integrity

`tesla_api/stream.ex:134-160`: a single `vehicle_disconnected` does **not** notify the vehicle FSM at
all — it only schedules a **resubscribe** (backoff `1.3^d`, max 8 s, *when last shift_state was
P/D/N/R*; else 15–30 s). Only after **10** consecutive disconnects does it emit `:too_many_disconnects`.
So firing it on the park edge cannot, by itself, split or close a drive.

### 3.3 Does TeslaMate handle `"invalid"` as a gear? — **No, and it never sees it**

- `Stream.Data.into!` maps `shift_state` via `to_s`: **`"" → nil`**, and **any other string passes
  through verbatim — there is no `"invalid"` case** (`tesla_api/stream/data.ex:24-25`). The FSM only
  pattern-matches `~w(D N R)` (drive) and `[nil, "P"]` (close). An `"invalid"` string would match
  *neither* drive nor close — it would be an inert non-driving sample (would NOT even close a drive).
- **But the add-on never sends `"invalid"`.** Both surfaces run `Gear` through `strip_state`, which
  maps `<invalid>`/`invalid`/`""` → `None` (fields.py:39-51). REST emits JSON `null`; the stream CSV
  emits `""` → TeslaMate `nil`. So TeslaMate is correctly shielded; the `"invalid"` question is moot
  in practice.

### 3.4 G1/G2 — the `<invalid>` Gear sentinel, and the user's proposed fix

**What the Store does today:** `state.py:124` skips the `<invalid>` sentinel for normal fields (keep
last good value) **except** for `LIVE_ONLY = (Gear, VehicleSpeed)`, where it *writes* `<invalid>` —
clobbering the gear to the sentinel, on the theory that "`<invalid>` is the parked signal."

**Live evidence says that theory is unnecessary.** From `/data/telemetry-log.jsonl` (49 `Gear`
samples): `ShiftStateP` ×17, `ShiftStateD` ×9, `ShiftStateR` ×8, `<invalid>` ×15 — and **every single
`<invalid>` is immediately preceded by an explicit `ShiftStateP`** (e.g. `…P(18:15:18) → <invalid>(:23)
→ R(:28)`). `<invalid>` *never* appears between driving gears; it only ever follows a park. So
`<invalid>` carries **no information that the preceding `ShiftStateP` didn't already**.

**Is it actively harmful today? No — but it's confusing and load-bearing on an assumption.** Because
the preceding `ShiftStateP` already flips `was_driving→False` in `StreamSink._broadcast` (stream.py:96-113),
the subsequent `<invalid>` event lands as a quiet no-op (`not driving and not was_driving → return`).
It is not double-disconnecting. The genuine deviation (**G2**) is broader and *by design*: **every**
transition to `P` — including the brief P of a parking-lot maneuver — fires `vehicle_disconnected`.
Per §3.1-3.2 that is tolerated (REST-driven close; short 8 s resubscribe; D resumes the drive). Live
DB confirms no pathological splitting: the maneuvering session is a single drive (1161), and drives
1160/1161/1163/1164 are cleanly separated.

**The user's proposal — "persist the last non-invalid gear, never set invalid" — is correct and
recommended (G1).** Concretely: drop the `and k not in LIVE_ONLY` exception at `state.py:124` so
`<invalid>` is skipped like every other field; `Gear` then retains its last *real* value, which after
a park is `"P"`. Keep `LIVE_ONLY` for the **seed**-skip (`state.py:176`, `_write_fleet`) — that part
is still needed so the Fleet seed never injects a stale gear.

- **Outcome is identical for drive-close:** REST reports `shift_state=null` for both `"P"`
  (not-in-D/R/N → None) and the old `<invalid>`→None, so `close_drive` still fires; the stream park
  edge still fires on the real `ShiftStateP`.
- **Benefit:** removes a redundant write/event, removes a special case, and removes the only path
  that could ever set gear to a sentinel mid-stream.
- **One caveat to state explicitly:** this relies on Tesla always sending an explicit `ShiftStateP`
  on park (49/49 observed). If a park were ever signalled by `<invalid>` *alone* (no preceding P),
  "persist last gear" would hold `"D"` and the drive would hang — but TeslaMate's **15-min
  `@drive_timeout_min`** (`vehicle.ex:44`) and the add-on's REST staleness backstop both close that
  pathological case. Net risk: strictly lower than today's behavior. Recommend pairing the change
  with a one-drive live validation.

---

## 4. Recommendations

1. ~~**G1:** stop writing the `<invalid>` Gear sentinel.~~ **Done (v1.0.24)** — `state.py:124` now skips
   `<invalid>` for all fields (persists last real gear); `LIVE_ONLY` kept at `state.py:176` for the
   seed-skip. Validate against one real drive.
2. ~~**SL1:** re-confirm a latched asleep/offline state.~~ **Done (v1.0.24)** — the monitor tick
   (extracted to `app/control/monitor.py`) re-polls `/products` once a confirm goes stale
   (`FT_SLEEP_RECHECK_SECS`, default 900 s), so offline↔asleep↔online transitions are picked up.
2. **S1:** confirm the stream timestamp should be Store receive-time vs telemetry `CreatedAt`; if
   `CreatedAt` is preferred, thread it through the Store event (`{"at": …}`) or read it in the sink.
3. **U1 (optional):** map the seeded/streamed `SoftwareUpdate*` into `vehicle_state.software_update`
   so TeslaMate's `updates` table populates.
4. **L1 (deferred):** harden gear normalisation with a `gear_letter` wrapper — not triggered by
   observed telemetry (only `ShiftState*`/`<invalid>` seen).
5. **No action:** G2 (`vehicle_disconnected` on every P) is by design and TeslaMate-tolerant; W1
   (no wake route) is the deliberate never-wake policy; the two charge fields and streaming power are
   DB-verified correct.
