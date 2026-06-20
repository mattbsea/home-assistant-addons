# Intelligent telemetry-config editor + Fleet-API call counter

## Context

The add-on streams a **fixed, hardcoded roster** of ~73 vehicle fields (`fields.TELEMETRY_FIELDS`,
name → `interval_seconds`) to the car via `fleet_telemetry_config`. Users can't see or tune it — to
change what's streamed or how often, you edit Python. This feature surfaces the roster in the wizard's
"Send vehicle config" step as an **intelligent editor** (pick a profile, then fine-tune per field), and
adds a **Fleet-API call counter** to the dashboard for usage visibility.

Principle (user): **nothing new may depend on `/data/fleet-log.jsonl` or `/data/telemetry-log.jsonl`** —
both are diagnostic and will be removed once the system is proven. The roster override persists in the
wizard config; the call counter is purely in-memory.

## Component 1 — telemetry-config editor

**UI (wizard "Send vehicle config" step, `wizard.html`): preset-first, advanced editor underneath.**
- **Profiles** (radio): *TeslaMate Complete* (= current 73, recommended) · *Low bandwidth* (essentials,
  longer intervals) · *Everything* (all curated on) · *Custom* (auto-selected on any manual edit).
- **Summary bar**: enabled count · estimated **max** msgs/min (Σ 1/interval over enabled fields) ·
  rate-limit headroom gauge vs `rate_limit_message_limit`/`rate_limit_message_interval`
  (default 1000 / 30 s). Labeled "estimated max" (on-change fields report below 1/interval).
- **Send to vehicle**: shows the diff count vs the roster currently on the car (compare the saved
  override's hash to the last-sent hash in shim-state). Reuses the existing `send_telemetry_config` check.
- **Advanced expander**: grouped accordion (Drive & Location, Battery & Charging, Climate,
  Body & Security, Tires, Software, Navigation, Other). Per group: enabled count + bulk "set interval".
  Per-field row: **on/off · interval (s) · ⭐TeslaMate badge** for essentials; disabling an essential
  shows a confirm/warn but is allowed. Search box filters. "Show all Tesla fields" reveals the rest of
  the proto enum (off by default; only reach raw Logger/MQTT/Pub-Sub).

**Backend data model (`fields.py`):**
- Keep `TELEMETRY_FIELDS` as `DEFAULT_ROSTER` (the *TeslaMate Complete* profile).
- `FIELD_GROUPS`: name → group label (covers every curated field).
- `ESSENTIAL_FIELDS`: set TeslaMate needs (Location, Soc, BatteryLevel, Gear, DetailedChargeState,
  Odometer, VehicleSpeed, PackVoltage, PackCurrent).
- `ALL_FIELDS`: the full proto `Field` enum (fetched from upstream `vehicle_data.proto`) for "show all".
- `PROFILES`: `{teslamate, low_bandwidth, everything}` → roster dicts (derived from DEFAULT_ROSTER).
- `effective_roster(override)`: `DEFAULT_ROSTER` overlaid by the user override → `{name: {interval_seconds}}`
  of **enabled** fields only (what gets sent to the car).
- `telemetry_fields_hash(roster=None)`: fingerprint of a given roster (defaults to effective) — gates
  auto-resend.

**Persistence:** the override is stored whole under a top-level `telemetry_roster` key in the wizard
config (`/data`), as `{Name: {enabled: bool, interval_seconds: int}}`, plus `telemetry_profile`. Written
by a dedicated endpoint that **replaces** the key (not deep-merged, so removing a field's override
works). `effective_roster` reads it via `cfgmod.load`.

**Wiring:** `sendconfig.build_request`/`send` already accept `roster=` — pass `effective_roster(...)`.
`autosend` and the `send_telemetry_config` check fingerprint/send the effective roster.

**API (`web/wizard.py`):**
- `GET /api/wizard/telemetry` → `{catalog: {groups, essential, all_fields, defaults}, override, profile,
  rate_limit}`.
- `POST /api/wizard/telemetry` → `{override, profile}` replace-saved; returns `{ok}`.

## Component 2 — Fleet-API call counter (dashboard)

- `Store` gains an in-memory counter (`total` + by-kind: token/products/vehicle_data) + a thread-safe
  `note_fleet_call(url)` and a `fleet_calls()` reader. **No persistence, since add-on start.**
- `app/main.py`: a tiny **counting wrapper** around `prime._post_form`/`_get`, applied **independently of**
  the `fleetlog` wrapper (so it survives the logs' removal): `counting → (logging) → real`.
- `web/api.py` `state_payload`: add `fleet_api: {total, vehicle_data, token, products, since}`.
- `dashboard.html`: a header pill `Fleet API: N` (with the `vehicle_data` subset shown — the billable one).

## Testing
- `effective_roster` merge (override on/off + interval); `PROFILES` integrity; `telemetry_fields_hash`
  changes when the roster changes; catalog integrity (every essential & curated field has a group; all
  curated ⊆ ALL_FIELDS); `build_request` uses the passed roster.
- `Store.note_fleet_call` counts by kind; `state_payload` exposes `fleet_api`.
- API: `GET`/`POST /api/wizard/telemetry` round-trip; `send_telemetry_config` builds from the effective
  roster.
- `uv run pytest` green. Wizard JS + dashboard pill: manual verify (static assets).

## Out of scope / notes
- Value-change thresholds (Tesla per-field min/max) — not exposed (chosen "on/off + interval").
- "Show all" fields that the add-on doesn't map won't appear in TeslaMate/dashboard (documented in the UI).
- Deploy only when the car is idle (restart resets the telemetry stream).
