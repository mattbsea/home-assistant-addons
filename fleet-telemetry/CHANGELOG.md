# Changelog

## 1.0.9

### Fixed
- **Software-update status now populates from the Fleet-API prime on start** (not only from the live
  stream). `vehicle_state.software_update` is already fetched by the prime; `prime_to_fields` now
  reverse-maps it to `SoftwareUpdateVersion` / `SoftwareUpdateInstallationPercentComplete` /
  `SoftwareUpdateDownloadPercentComplete`, so the Vehicle tile's Update row is correct immediately
  after a restart. (`EnergyRemaining` stays telemetry-only — the Fleet API exposes no energy-in-kWh
  field, only %/range — so it fills within ~30s of streaming.)

## 1.0.8

### Added / Fixed (dashboard)
- **Surface the v1.0.7 telemetry fields on the dashboard.** "Energy left" (`EnergyRemaining`, kWh) now
  populates in the Battery tile. The Vehicle tile shows a software **Update** row, and the Tire tile
  shows TPMS **Warnings** — both displayed only when actually present.
- **No more phantom "installing 1%".** The software-update percent fields report a stray idle value
  (e.g. install = 1) when nothing is updating, so the Update row only appears when a real
  `SoftwareUpdateVersion` is staged (then shows downloading/installing %); otherwise it's hidden.
- The new fields are added to the dashboard's grouped set so they render in their proper tiles
  instead of the raw "Other signals" list.

## 1.0.7

### Added
- **Auto re-send of `fleet_telemetry_config` when the requested-field roster changes.** The add-on
  now fingerprints `TELEMETRY_FIELDS` and stores the last successfully-sent hash in `shim-state.json`
  (the unwatched file). After each prime, if the roster differs (e.g. after an upgrade that adds
  fields), it re-sends the config to the car automatically — no manual "Send to Vehicle" needed. A
  rejected send is **inert** (verified: Tesla validates the config atomically and the car keeps its
  existing config), so a bad roster field can't drop telemetry; the failed send is logged and retried.
- **More telemetry fields requested** (from a completeness review): `EnergyRemaining` (battery energy
  in kWh), `SoftwareUpdateVersion` + install/download percent (OTA status), and `TpmsHardWarnings` /
  `TpmsSoftWarnings` (the car's own tire alerts). `Location` interval tightened 30s → 10s for usable
  drive traces. (These reach the car via the auto-resend above.)
- **`gui_settings` retained and mapped.** It was fetched from the Fleet API then discarded; it's now
  kept and `SettingTemperatureUnit` / `SettingDistanceUnit` are surfaced in the superset.

### Fixed
- `sendconfig.send` now returns the rotated refresh token on failure too (it rotates during the token
  refresh regardless of whether the POST succeeds), so a failed send can't strand a stale token. The
  wizard's manual send persists it on both paths and records the roster hash on success.

## 1.0.6

### Fixed
- **Close superset coverage gaps found by a field-completeness review** (code-only; no change to what
  the car streams):
  - **Live charge rate (and 3 more) no longer masked by a stale prime.** `assemble()` now forward-emits
    `charge_rate` (from `ChargeRateMilePerHour`), `charge_port_latch`, `cabin_overheat_protection`, and
    `vehicle_name` from live telemetry. Previously these were never emitted, so the shim's
    `vehicle_data` always took them from the up-to-30-min-stale prime snapshot — during a charge the
    live rate was silently discarded.
  - **Trip/navigation fields now fill from the Fleet-API prime.** `prime_to_fields` now reverse-maps
    `DestinationName`, `DestinationLocation` (from `active_route_latitude/longitude`),
    `RouteTrafficMinutesDelay`, and `ExpectedEnergyPercentAtTripArrival`, so the dashboard's
    Destination/ETA/traffic/energy-at-arrival rows populate on a parked/just-restarted car.

### Deferred to a follow-up release (touch the vehicle config/command path — verified separately)
- Telemetry roster expansion (`EnergyRemaining`, software-update + TPMS-warning fields, tighter
  `Location` interval) and `gui_settings` mapping — these change `fleet_telemetry_config` and require a
  re-send to the car.
- Auto re-send of `fleet_telemetry_config` when the requested-field roster changes (hash tracked in
  `shim-state.json`).
- Skipped intentionally: synthesizing `DefrostMode`/`HvacPower` from the prime (uncertain value
  semantics — a wrong value is worse than a blank row).

## 1.0.5

### Changed
- **Unified data model: the Store is now the single superset both sources refresh and both
  consumers read.** Previously the dashboard read only live telemetry while the Fleet-API "prime"
  snapshot lived in a separate table that only the TeslaMate shim merged (at request time). After a
  restart — or any time a parked car streams almost nothing (telemetry is on-change) — the dashboard
  was nearly empty even though a full Fleet-API snapshot was available.
  - The Fleet-API prime now lives in the Store alongside live telemetry (one structure per VIN).
  - `Store.merged_fields()` exposes the superset: prime as the base layer, overlaid by live telemetry
    (which always wins). The dashboard renders this, so a freshly-restarted/parked car shows a full
    picture immediately.
  - Ephemeral drive fields (`Gear`/`VehicleSpeed`) are never sourced from the prime (v1.0.4
    principle), so the dashboard never shows a stale gear/speed.
  - The TeslaMate shim keeps its own ephemeral-safe `vehicle_data` projection (it needs prime-only
    fields like vehicle config/media that don't map to telemetry names); it now reads the prime from
    the Store. Each dashboard field also carries its `source` (`telemetry`/`prime`) and the payload a
    `prime_epoch`, for future "live vs snapshot" indicators.

## 1.0.4

### Fixed
- **TeslaMate no longer gets stuck in "driving" after you park.** On park, Tesla streams
  `Gear: "<invalid>"` (its parked sentinel), not `"P"`. Two bugs turned that into a permanently
  open drive:
  - The streaming-ws builder treated a `None` gear as "not ready yet" and **suppressed the frame
    entirely**, so TeslaMate never received the park transition — its last frame still said
    `shift_state=D`. It now emits the frame with `shift_state=""` once a gear has been seen, so a
    park ends the drive (frames are still suppressed before the first gear is ever seen).
  - The Fleet-API shim **backfilled `shift_state`/`speed` from the prime snapshot**, which can be up
    to one re-prime interval (30 min) stale. A poll after parking kept reporting "driving at 38" from
    a snapshot captured mid-drive. Ephemeral drive-state fields are now live-or-nothing — never
    inherited from prime. (Static/charge/climate fields still backfill from prime as before.)

## 1.0.3

### Fixed
- **Hardened reliability after a code review.** Four latent failure modes:
  - A malformed telemetry record (non-dict `data`) would raise inside ingest and kill the single
    records-tail thread, silently freezing every surface. Such frames are now skipped; the tail also
    isolates per-record errors and self-restarts if it ever exits.
  - The four async listeners (dashboard/wizard, shim, streaming ws, pubkey) plus the stream sink ran
    under a single `asyncio.gather` with shared fate — one crash took the rest down. Each is now
    supervised independently and restarts on failure.
  - The records JSONL in `/tmp` (tmpfs/RAM) grew unbounded, truncated only at boot. It is now capped
    (200 MB) and truncated in place when exceeded.
  - `charge_energy_added` in the Fleet-API shim always read an always-`None` baseline slot, so it
    never reported energy added during a charge. The baseline is now tracked live by the Store at
    charge-session start and read from there.

## 1.0.2

### Fixed
- **Telemetry no longer drops when the refresh token rotates.** Tesla rotates the refresh token on
  every use (priming, send-config). The v1.0.0/1.0.1 send-config route persisted the rotated token
  to `wizard-config.json` — the file `run.sh` watches as its restart signal — which bounced the
  fleet-telemetry binary and dropped the vehicle's mTLS connection (telemetry gap until Tesla
  reconnects). Operational token rotations now go to `shim-state.json` (unwatched), matching v0, so
  the binary is never restarted by a token rotation. `wizard-config.json`'s `shim_refresh_token`
  stays only the initial OAuth seed.

## 1.0.1

### Fixed
- **Wizard 404 / dashboard endpoints.** The v1.0.0 rewrite served the dashboard/wizard pages but
  exposed the wrong API paths — the (verbatim) UI calls `/setup` and `/api/wizard/*`
  (config/save/state/hostports/keypair/oauth-url/oauth-exchange/register-partner/npm-proxy-host/
  npm-stream/check), and `/` redirects to `./setup` until setup completes. Restored that exact
  contract, and ported the wizard-state, host-port lookup, and pubkey/cert/records checks that were
  missing. The setup wizard works again.

## 1.0.0

### Changed — unified app rewrite (no user-facing setup changes)

The add-on's glue layer was rewritten into a **single async Python app** (`app/main.py`): one
records tail feeds one in-memory per-VIN **Store** + event bus, and every surface is served from it
— the **dashboard + setup wizard** (ingress), the **Fleet-API shim** (`:8085`), the **TeslaMate
streaming websocket** (`:8081`), and the **public-key** `.well-known` listener (`:8100`).

This replaces the previous four glue processes (separate dashboard, shim, bridge, and a **Node**
websocket server) and removes their duplication:

- **Node removed** — the TeslaMate streaming server is now native Python (`websockets`).
- **One records reader** instead of three near-identical tail loops.
- **One field model** (`fields.py`) — the meta-key sets, enum/gear/location decoders, the
  telemetry↔vehicle_data mapping, and the telemetry field roster live in exactly one place.
- **One NPM client** — the bash `fetch-npm-cert.sh` is gone; cert fetch + proxy-host/stream
  provisioning are unified in Python.
- ~2,800 lines of duplicated/dead code removed; the app ships with a `pytest` suite.

The setup wizard, configuration schema (`wizard-config.json`), and all integrations (TeslaMate
shim + streaming, MQTT, Pub/Sub) are unchanged — no reconfiguration needed on upgrade.

## 0.10.16

### Fixed
- **Door rows no longer pulse "closed" every tick.** The change-pulse animation keyed rows by
  label text alone; after the Doors/Windows split both tiles have rows labelled "Front left" etc.,
  so the keys collided and the door rows flashed on every refresh even with unchanged data. Rows
  are now keyed by tile title + label.

### Changed
- **Map fills its tile.** The map card is now a flex column and the iframe grows to fill the
  available height, so it matches the height of neighbouring tiles instead of leaving a gap below a
  fixed-height map.

## 0.10.15

### Removed
- **All car-wake code.** The shim no longer ever calls Tesla's `wake_up`. `prime_once()` simply
  skips any vehicle that isn't already `online` and reads `vehicle_data` only for awake cars.
  Removed `_wake`, the `shim_wake_on_prime` option, the `FT_SHIM_WAKE_ON_PRIME` env, and its
  `run.sh` plumbing. (Fleet API `vehicle_data` is read-only and never woke a car anyway; the prior
  `wake_up` attempt was also being rejected with HTTP 406.) Wake the car from the Tesla app if you
  want a fresh cold-start snapshot.

### Changed
- **Dashboard: map tile now flows with the other tiles.** The Location/map card lives inside the
  tile grid as a 2-column-wide tile, so it sits beside other cards when the window is wide instead
  of always dropping to its own full-width row. Collapses to a single column on narrow screens.
- **Dashboard: split "Doors & windows" into separate "Doors" and "Windows" tiles**, each listing
  one labelled row per door/window (Front left / Front right / Rear left / Rear right, plus Frunk
  and Trunk for doors). Every door now shows its own open/closed state — previously a single summary
  row could obscure multiple open doors.
- **Dashboard: Destination shows "Not Set"** when there is no active route (empty / `None` / null),
  instead of hiding the row.

## 0.10.14

### Fixed
- **"Send to Vehicle" no longer fails with `unknown field NetworkInterface`.** v0.10.13 added
  `NetworkInterface` to the streamed telemetry config, but that name is not part of Tesla's
  telemetry `Field` enum, so the vehicle rejected the entire `fleet_telemetry_config` payload.
  Removed it from the config. The other seven fields restored in v0.10.13 are valid and remain.
  `NetworkInterface` had no `vehicle_data` prime source either, so the dashboard "Network" row was
  never going to populate — no functionality is lost. **Re-send the vehicle config (wizard step 13).**

## 0.10.13

### Fixed
- **Restored telemetry fields the dashboard expects but the refactor had dropped.** Added back to
  the vehicle telemetry config (and the cold-start prime where available): `ChargeRateMilePerHour`
  (charging mi/h), `ChargePortLatch`, `CabinOverheatProtectionMode`, `VehicleName`,
  `NetworkInterface`, and `LocatedAtHome` / `LocatedAtWork` / `LocatedAtFavorite` (the 🏠/🏢/⭐ map
  label). These rows were always blank because the fields were never requested from the vehicle.
  **Re-send the vehicle config (wizard step 13) to start streaming them.**
  (`ChargeState` is intentionally a fallback for the streamed `DetailedChargeState`; `EnergyRemaining`
  has no telemetry source and stays hidden.)

## 0.10.12

### Fixed
- **Window states showed a bare "0".** Window fields arrive as a number (0 = closed, >0 = open) or
  an enum string; the dashboard now maps them to "closed" / "vented" / "open" instead of printing
  the raw value.

## 0.10.11

### Changed
- **Dashboard: categorized the "Other signals" fields into proper tiles.** Pack voltage/current,
  battery heater and low-power-to-heat now sit in Battery; requested/max charge current in Charging;
  driver/passenger set temps, preconditioning, defrost and rear defrost in Climate; model, trim,
  color and wheels in Vehicle. Added a new **Navigation** tile (destination, ETA, traffic delay,
  energy at arrival, route updated) and it **parses `DestinationLocation`'s lat/lon JSON** into a
  clickable map link. "Other signals" now only shows genuinely unknown fields.
- **Wizard: the `tesla.com/_ak/<domain>` pairing link is now clickable** (opens in a new tab), in
  addition to the copy box.
- **Dashboard: the value-changed pulse now fades over 2s instead of 5s.**

## 0.10.10

### Added
- **New "Pair Virtual Key" step** before sending the vehicle telemetry config. Tesla requires the
  owner to approve the app's key on each vehicle (via the `https://tesla.com/_ak/<domain>` deep
  link in the Tesla app) before it accepts any signed command, so the `fleet_telemetry_config` send
  would otherwise fail. The step shows the copyable deep link and is gated before the send step.

## 0.10.9

### Fixed
- **Step 9 "Create Stream" hung indefinitely.** `server.py` referenced a `TELEMETRY_PORT` constant
  that was never defined (it only existed as a shell variable in `run.sh`), so the stream-creation
  and host-ports endpoints raised a `NameError` and returned no response — the wizard sat on
  "Creating the Stream in NPM…". Defined `TELEMETRY_PORT` (default 4443, overridable via
  `FT_TELEMETRY_PORT`, exported from `run.sh`).

## 0.10.8

### Changed
- **Step 9 "Create the Stream" text updates live as you change the telemetry port.** The displayed
  public port (in the description, router-forward note, and manual instructions) now updates on each
  keystroke without re-rendering the step, so it always reflects the value in the port field.

## 0.10.7

### Changed
- **Auto-detect the add-on's host-mapped ports for the NPM forward target.** The stream and
  public-key proxy host now forward to the actual host port the add-on is mapped to (read live from
  the Supervisor `/addons/self/info` Network settings) instead of assuming a 1:1 mapping — so it's
  correct even if you remap ports in the Network tab. Step 9 now shows the exact forward target
  (`<forward_host>:<host_port>`), and notes the host is your NPM setting while the port is detected
  from the add-on settings.

## 0.10.6

### Added
- **Step 9 auto-creates the NPM telemetry Stream.** Like the public-key proxy host, the wizard now
  creates the TCP-passthrough Stream for you via the NPM API (incoming telemetry port → HA host
  `:4443`, no SSL termination). Reuses an existing stream on that port if present. Manual
  instructions remain available as a fallback under a collapsible section.

## 0.10.5

### Security
- **Bounded the public-key reachability check against SSRF.** v0.10.4 relaxed the resolved-IP guard
  to support split-horizon DNS, which would also have allowed probing loopback/link-local targets
  (e.g. `169.254.169.254`). The check now hard-rejects loopback, link-local, multicast, unspecified
  and reserved addresses, allows only RFC1918 private IPs (the legitimate LAN-hairpin case) with a
  warning, relies on TLS certificate verification as the guard against reading data from internal
  hosts, and no longer echoes raw connection errors (removing a port-scan oracle).

## 0.10.4

### Fixed
- **Public-key check no longer fails on split-horizon DNS / NAT hairpin.** Inside many LANs the
  public domain resolves to a private IP, which made the step-6 reachability test reject it with
  "Domain must resolve to a public IP address". The private-IP case is now a non-blocking
  **warning**; the check still fetches the key and additionally verifies the served key matches the
  one the add-on generated. A note reminds you to confirm the domain is reachable from the public
  internet so Tesla can fetch it.

## 0.10.3

### Changed
- **Reordered the wizard so the domain/public key is set up before Tesla credentials.** Tesla's
  developer portal only issues a Client ID/Secret once it can validate the public key at your live
  domain, so entering credentials first was a dead end. New order: NGINX Proxy Manager → generate
  signing key → public-key domain + auto proxy host → verify public key → **then** create the Tesla
  app and paste its Client ID/Secret → register partner. The Tesla-app step now shows the exact
  Allowed Origin and Redirect URI to use.

## 0.10.2

### Fixed
- **Wizard "Next" stayed disabled after filling fields.** The credential steps gate Next on the
  config object, but inputs only wrote to it on blur and never refreshed the nav, so a fully
  filled form left Next greyed out. Inputs now update live on every keystroke (`oninput`) and
  refresh the nav immediately; the domain is also flushed to the server before the proxy-host call.

## 0.10.1

### Fixed
- **Fresh-install startup crash.** The bashio interpreter runs scripts with
  `errexit`/`nounset`/`pipefail` enabled; on a fresh install (no config file yet) the supervision
  loop's first benign non-zero test silently killed `run.sh` right after the wizard started, so
  the add-on exited (state "error") before the wizard was usable. `run.sh` now explicitly disables
  those strict modes (it handles its own errors), so the wizard stays up on a clean install.

## 0.10.0

### Changed (breaking — re-run setup)
- **Wizard-driven configuration.** The add-on Configuration page is gone. Everything is now
  configured through the built-in setup wizard, which writes a single local config file
  (`/data/wizard-config.json`). Existing installs are **not** migrated — open the add-on and run
  the wizard again. Back up your old option values first if you need them.
- **Inverted startup.** The setup wizard/dashboard now always starts first and never fails on a
  fresh, unconfigured install. The TLS-cert fetch, the `fleet-telemetry` binary, the TeslaMate
  shim and the bridge are started reactively once the wizard has written enough config, and
  restart automatically when settings change — no add-on restart needed.

### Added
- **Add-on generates and hosts the signing keypair.** The wizard creates the EC key pair, serves
  the public key on a dedicated internet-facing listener (port `8100`), and **auto-creates the
  NGINX Proxy Manager proxy host** (HTTPS :443 + Let's Encrypt) for the `.well-known` path.
- **Automated Tesla partner registration** (client-credentials grant) and a **guided OAuth login**
  that captures the user-context refresh token via the authorization-code flow.
- **Single-domain setup.** Because the telemetry port is configurable, one domain can serve both
  the public key (proxy host on :443) and the telemetry stream (NPM Stream on the chosen port).

### Removed
- All `options:`/`schema:` entries from `config.yaml`, and the `share:ro` mapping (the private key
  is now generated at `/data/keys/private-key.pem`).

## 0.9.9

### Fixed
- **Shim `drive_state.power` is now an integer.** TeslaMate's Ecto schema requires power as
  `integer` (kW, rounded), but the shim was returning a float (e.g. `21.9`), causing an Ecto
  changeset validation error and a TeslaMate crash loop on every drive poll.

## 0.9.8

## 0.9.7

### Changed
- **Wizard step 9 now has a configurable port field** (default 4443). The port is saved to
  wizard state, shown in the example JSON preview, and passed through to the vehicle config.

## 0.9.6

### Fixed
- **Telemetry config now uses port 4443** (direct mTLS, bypassing NPM). Port 443 routed through
  NPM's HTTPS proxy, which terminates TLS and breaks mTLS. Port 4443 is forwarded at the router
  level directly to the add-on, preserving the end-to-end TLS required for vehicle connections.

## 0.9.1

### Changed
- **Wizard step 9 now sends the telemetry config directly** instead of showing a curl command.
  A "Send to Vehicle" button calls the add-on backend, which uses the stored shim credentials
  and app private key via `tesla-http-proxy`. On success step 9 is auto-marked done.

## 0.9.0

### Changed
- **`fleet_telemetry_config` now uses the correct signing flow.** The endpoint requires JWS command
  signing via `tesla-http-proxy` — a plain Bearer-token POST always returns a generic 404. The
  function now uses the `teslamate_shim_*` refresh token (user-context, `grant_type=refresh_token`)
  instead of the developer client credentials, routes the request through a locally spawned
  `tesla-http-proxy` instance that signs the payload with the app EC private key, and targets
  the bulk `POST /api/1/vehicles/fleet_telemetry_config` endpoint with a `vins` array. The
  `developer_*` credential options remain in the configuration for partner account operations.

### Added
- **Bundled `tesla-http-proxy` binary** (`vehicle-command v0.4.1`, built from source in a Go
  multi-stage Dockerfile stage). The binary is installed at `/usr/local/bin/tesla-http-proxy`.
- **`share:ro` volume mount** — the add-on now reads the app EC private key from
  `/share/tesla-fleet/private-key.pem`. Place your Tesla developer private key there before
  triggering `send_telemetry_config`.

## 0.8.8

### Added
- **Dedicated developer credential options** (`developer_client_id`, `developer_client_secret`,
  `developer_fleet_api_host`) in the add-on Configuration tab. These are used for the Tesla
  client credentials OAuth grant (`grant_type=client_credentials`) which authenticates the
  developer/partner directly — no user token involved. Required for partner-level operations:
  sending `fleet_telemetry_config` to vehicles and managing the partner account registration.
  The TeslaMate shim credentials (`teslamate_shim_*`) remain separate — those use the
  authorization code flow (user refresh token) for vehicle data polling.

## 0.8.2

### Bug Fixes
- **`send_telemetry_config` now actually works.** The Tesla shim credentials (`client_id`,
  `refresh_token`, `fleet_api_host`) were not being exported into the server process's environment
  in `run.sh` — they were only set before the shim process launched. Fixed by exporting the full
  set of `FT_SHIM_*` env vars before the server is started.

## 0.8.1

### Added
- **`send_telemetry_config` wizard check endpoint.** `POST /api/wizard/check` with
  `{"check":"send_telemetry_config","domain":"...","region":"na"}` uses the stored Tesla credentials
  to refresh an access token, then POSTs the full 57-field `fleet_telemetry_config` to every vehicle
  in the account. CA chain is read from the existing NPM cert file and injected automatically.

## 0.8.0

### Added
- **Massively expanded TeslaMate data coverage.** The shim now maps significantly more streaming
  telemetry fields to TeslaMate's Fleet API response format, eliminating data gaps that were
  previously only filled (partially) by the prime snapshot:
  - **Charge state**: `battery_heater_on`, `not_enough_power_to_heat`, `charge_current_request`,
    `charge_current_request_max`, `charge_port_door_open` — TeslaMate stores these in both the
    `charges` and `positions` tables; they were previously absent from streaming responses.
  - **Climate state**: `is_preconditioning`, `is_front_defroster_on`, `is_rear_defroster_on`,
    `battery_heater` — all four were missing from the assembled climate response.
  - **Vehicle state**: `fd_window`, `fp_window`, `rd_window`, `rp_window` — window positions
    (0=closed, 1=venting, 2=open) now populated from streaming `FdWindow`/`FpWindow`/`RdWindow`/
    `RpWindow` enum fields.
  - **Drive state**: `active_route_destination`, `active_route_miles_to_arrival`,
    `active_route_minutes_to_arrival` — now populated from the streaming `DestinationName`,
    `MilesToArrival`, `MinutesToArrival` fields (not only from the prime snapshot).
- **Two new helper functions** in the shim (`_window_state`, `_defrost_on`) that parse the
  enum-string format Tesla streaming uses for windows and defrost mode.
- **`_prime_to_fields()`** in the dashboard extended to map the same new fields from the prime
  snapshot into the streaming field namespace, so the dashboard displays them correctly on startup
  before the live stream has supplied them.
- **Wizard fleet_telemetry_config expanded from 12 to 57 fields.** The recommended config in
  setup wizard Step 9 now includes every streaming field the shim reads: energy counters
  (`ACChargingEnergyIn`, `DCChargingEnergyIn`), pack power (`PackVoltage`, `PackCurrent`), all
  range estimates, charger detail, battery heater state, preconditioning, defroster, window
  positions, GPS heading, TPMS pressures, odometer, firmware version, lock/sentry, and door/window
  states. Users who re-send the config to their vehicle unlock full data capture.

## 0.7.10

### Added
- **Navigation destination and ETA in the Drive card.** The dashboard now shows the active navigation
  destination and estimated arrival (miles + minutes) when the car is navigating. The data comes
  from the prime snapshot immediately on startup, and from live streaming telemetry once the
  vehicle's `fleet_telemetry_config` includes the new fields. The suggested config in the setup
  wizard (Step 9) now includes `Destination`, `MilesToArrival`, and `MinutesToArrival` — re-send
  the config to your vehicle to enable live updates. Until then, the destination shown reflects
  the prime snapshot (updated on each add-on restart).

## 0.7.9

### Bug Fixes
- **Charging state no longer stuck at stale "Disconnected" after add-on restart.** On warm-start,
  `DetailedChargeState` and `ChargeState` are now cleared from the reloaded fields, joining the
  movement fields (`Gear`, `VehicleSpeed`, etc.) dropped in v0.7.8. Without this, a shim restarted
  while the car was driving would warm-start with `DetailedChargeState=Disconnected`, and the prime
  (fetched fresh from Tesla on each startup) could not override it — because the prime fill-in only
  applies when the assembled value is `null`. TeslaMate would therefore see `charging_state:
  Disconnected` even while the car was actively charging on arrival, missing the entire charging
  session until the first new stream record arrived. Clearing these on warm-start lets the fresh
  prime supply the correct `charging_state` immediately.

## 0.7.8

### Bug Fixes
- **Stale gear/speed no longer served after add-on restart.** On warm-start, the shim reloads
  its persisted fields — including `Gear` and `VehicleSpeed` from the last telemetry record before
  shutdown. If the car was moving when the add-on restarted and then went quiet (e.g., parked and
  slept immediately), the shim would keep serving the old `shift_state=R, speed=3 mph` data for
  up to 11 minutes (the online-window expiry), causing TeslaMate to log the parked car as still
  driving. Fixed by clearing `Gear`, `VehicleSpeed`, `PackCurrent`, and `PackVoltage` from the
  warm-started state — these are instantaneous and meaningless across a restart. Static accumulated
  fields (SoC, Location, Range, etc.) are retained as before.

## 0.7.7

### Bug Fixes
- **Prime snapshot `<invalid>` strings no longer passed through to TeslaMate.** When live telemetry
  hasn't yet provided a value, `vehicle_data()` fills gaps from the prime snapshot. However the prime
  — a real Tesla Fleet API response — contains `"<invalid>"` strings for fields like
  `conn_charge_cable`, `fast_charger_brand`, and `fast_charger_type` when the car is not charging.
  These were being forwarded to TeslaMate as-is instead of treated as absent. Fixed by skipping
  `"<invalid>"` and `"invalid"` values in the prime fill loop (same sentinel values that
  `_strip_state()` already filters for live telemetry fields).

## 0.7.6

### Bug Fixes
- **`drive_state.power` sign was inverted.** `PackCurrent` in Tesla's streaming telemetry is
  negative when the battery is discharging (driving) and positive during regen — the opposite of
  the Fleet API's `drive_state.power` convention (positive = consuming, negative = regenerating).
  The shim was computing `pv × pc / 1000` which gave negative power the entire time the car was
  driving, causing TeslaMate to log every trip as net-regeneration. Fixed to `-pv × pc / 1000`.

## 0.7.5

### Bug Fixes
- **`charge_limit_soc` now tracks live telemetry.** The shim's `_assemble()` was not reading
  `ChargeLimitSoc` from the stream, so TeslaMate always saw the prime snapshot value (e.g. 80%)
  even when the car's actual limit changed (e.g. to 50%). Fixed by including it in the assembled
  `charge_state`.

## 0.7.4

### Bug Fixes
- **Dashboard blank after v0.7.2.** `HvacFanStatus` arrives from the live telemetry stream as an
  integer (e.g. `8`), but the fan row lambda called `.replace()` on it — integers don't have
  `.replace()`, causing a TypeError that crashed `buildCards()` and left the entire vehicle grid
  empty. The lambda now handles both integer values (`8` → `"Speed 8"`, `0` → `"Off"`) and enum
  strings.

## 0.7.3

### Bug Fixes
- **Prime snapshot now fills in missing Climate and Battery fields.** `_prime_to_fields` was not
  mapping `fan_status` → `HvacFanStatus`, `driver_temp_setting` → `HvacLeftTemperatureRequest`,
  `passenger_temp_setting` → `HvacRightTemperatureRequest`, `charge_limit_soc` → `ChargeLimitSoc`,
  `charger_phases` → `ChargerPhases`, or `fast_charger_present` → `FastChargerPresent`. The
  dashboard now shows all of these from the initial prime rather than waiting for live telemetry.
- **Shim `fan_status` was always null when derived from streaming telemetry.** `HvacFanStatus`
  arrives as an enum string (e.g. `HvacFanStatusSpeed3`) but the shim passed it to `_round_int()`
  which can't parse strings. Fixed with a dedicated `_fan_speed()` parser.

## 0.7.0

### Added
- In-app onboarding wizard at `/setup` — guides new users and TeslaMate migrators through the full setup process step by step
- Auto-redirects to wizard on first install (before wizard is marked complete)
- Live public key URL check for Tesla developer app setup (Step 4)
- Live certificate verification check from within the wizard (Step 8)
- Pre-generated copy-paste payloads: EC key commands, partner registration curl, fleet_telemetry_config JSON and curl
- TeslaMate integration path selector with pre-filled env var blocks (Step 10)
- Real-time verification screen polls for certificate and first telemetry record (Step 11)
- "Setup Guide" button in dashboard header for post-setup reference
- Wizard state persisted to `/data/wizard-state.json` — resume mid-flow after add-on restart

## 0.6.6

### 🐛 Bug Fixes
- **`charge_energy_added` was double-counting energy.** `ACChargingEnergyIn` (AC wall draw) and
  `DCChargingEnergyIn` (energy stored in battery) measure the same energy at opposite sides of
  the onboard AC→DC converter — summing them counted each kWh twice. The shim now uses only
  `DCChargingEnergyIn` (battery-stored, matching what Tesla's Fleet API reports), falling back to
  AC if DC hasn't arrived yet.

## 0.6.5

### 🐛 Bug Fixes
- **`charger_power` is now an integer (kW).** The shim was returning a float which TeslaMate's
  changeset rejected with "is invalid". Fixed to match the real Fleet API's integer type.

## 0.6.4

### ✨ Improvements
- **Auth-free TeslaMate operation.** The shim now accepts `POST /token` requests and returns a
  synthetic bearer token (`qts-` prefix, 8 h expiry). Point TeslaMate's `TESLA_AUTH_HOST` at the
  shim (`http://<addon-host>:8085`) and sign it in with any dummy tokens — it will "refresh"
  against the shim forever, making zero real Tesla auth calls. The shim's own priming still uses
  the real add-on credentials directly.

## 0.6.3

### ✨ Improvements
- **Dashboard now reflects the shim's primed snapshot.** The ingress dashboard reads the shim's
  persisted state and fills any gaps the live stream hasn't provided (and shows fields telemetry
  never carries — car type/trim/color, real display name). Live telemetry still wins for everything
  it provides; the prime just makes the dashboard complete immediately after a restart instead of
  looking empty while slow fields trickle in.

## 0.6.2

### ✨ Improvements
- **Priming wakes a sleeping car** to grab a complete fresh snapshot on startup (add-on restarts are
  rare, so the battery/cost impact is negligible and you always get full data). New option
  `teslamate_shim_wake_on_prime` (default on) — set off to keep the old behavior of skipping cars
  that are asleep. `wake_up` is exempt from command-signing; if Tesla rejects it the shim gives up
  gracefully and falls back to telemetry.

## 0.6.1

### 🐛 Bug Fixes
- **Synthetic vehicle IDs now fit in IEEE-754 safe-integer range (< 2^53)**, like real Tesla IDs, so
  JSON consumers that parse numbers as doubles can't silently round them. (Elixir/TeslaMate was
  unaffected, but it's the correct, portable behavior.)

## 0.6.0

### ✨ Shim: generic identity + optional self-priming
- **No hardcoded vehicle identity.** The shim now discovers vehicles from the telemetry stream (and
  from the prime), supports multiple cars, and synthesizes a stable id/vehicle_id deterministically
  from each VIN (TeslaMate dedups by VIN and just echoes the id back). Nothing vehicle-specific is
  baked into the image — the add-on works for any TeslaMate user.
- **Optional self-priming.** Set `teslamate_shim_client_id` + `teslamate_shim_refresh_token` (your
  own Tesla app credentials) and on startup the shim makes **one** real Fleet-API call to discover
  the vehicle list and prime a complete snapshot per *online* car — including fields telemetry never
  carries (`vehicle_config`, display name, etc.). Live telemetry overlays the dynamic fields. The
  call is **wake-guarded** (skips cars that are asleep) and costs ~$0.002 per restart. Rotated
  refresh tokens are persisted; `teslamate_shim_fleet_api_host` selects the region (defaults to NA).
  Leave the credentials blank to run telemetry-only.

## 0.5.1

### 🐛 Bug Fixes
- **Shim readiness no longer requires `Odometer`.** A parked car doesn't re-emit `Odometer` (it only
  ticks on its timer / while driving), so requiring it kept a parked car pinned to `asleep` after a
  cold start. Readiness now needs only battery + location; `Odometer` flows into `vehicle_data` when
  present and persists across restarts.

## 0.5.0

### ✨ New: Fleet-API shim for TeslaMate (experimental)
- A new local HTTP server on **:8085** answers the three read-only Fleet API endpoints TeslaMate v4
  polls (`/api/1/products`, `/api/1/vehicles/{id}`, `/api/1/vehicles/{id}/vehicle_data`), assembling
  the response entirely from the live telemetry stream. Point TeslaMate's `TESLA_API_HOST` at this
  add-on and set each car's **`use_streaming_api = false`**, and TeslaMate gets its data from here at
  **zero Fleet-API cost** (streaming is free; this never polls Tesla). Token refresh still goes to
  real Tesla via `TESLA_AUTH_HOST`.
- **Cold-start safe**: reports the car `asleep` until the essential fields are present (and answers
  `vehicle_data` with a fuse-safe `408` if asked early), so TeslaMate keeps polling and never
  receives a half-populated snapshot. Latest state is checkpointed to `/data` for warm restarts.
- Still validating power sign/scale, charge-energy deltas, and gear/charge-state strings against a
  real drive + charge; those mappings are isolated.

### 🐛 Bug Fixes
- **Split "Doors & windows" out of Security and de-cram the windows.** Windows used to render as one
  jammed cell (`FL PartiallyOpen, FR PartiallyOpen, …`). Now Security (locked/sentry) is its own card,
  and each window gets its own labelled row (Front left / Front right / Rear left / Rear right) with a
  humanized status (`PartiallyOpen` → `Partially Open`).

## 0.4.6

### ✨ Dashboard reorganization
- With the expanded telemetry field set, every extra signal was piling into one "Other signals"
  box. The dashboard now groups them into themed cards: **Battery** (SoC + range + energy + limit),
  **Charging** (full detail only while charging, compact state otherwise), **Drive** (speed/gear/
  heading/odometer), **Climate**, **Security & access** (locks/sentry/doors/windows), **Tire
  pressure**, and **Vehicle** (name/VIN/software/network).
- **Cleaner values**: verbose telemetry enums are shortened (`DetailedChargeStateDisconnected` →
  `Disconnected`, `WindowStateClosed` → `Closed`), the `<invalid>` sentinel is hidden instead of
  shown, `DoorState` is parsed to friendly names (incl. Frunk/Trunk), and units follow the car's
  settings (°F, psi, mph, mi). "Other signals" now only shows genuinely ungrouped fields.

### 🐛 Bug Fixes
- **Merge the duplicate "Location" / "Location map" sections into one.** The coordinates and the
  map are now a single "Location" card; the coordinate text updates in place each refresh while
  the map iframe is still only reloaded when the vehicle moves (no flashing).

## 0.4.4

### 🐛 Bug Fixes
- **Fix dashboard `/api/state` crash (`KeyError`) when a vehicle's first record is location-only.**
  A vehicle could appear in the latest-values map before it had any SoC/speed history (e.g. the
  stream's first post-restart record carries only `Location`), making `build_state` raise a
  `KeyError` and the dashboard return 500. History lookups now default safely.

## 0.4.3

### 🐛 Bug Fixes
- **Dashboard map no longer flashes on refresh.** The page used to rebuild its entire contents
  every 5 seconds, which destroyed and recreated the OpenStreetMap embed each time — making the
  map reload (flash) on every poll even when the vehicle hadn't moved. The dashboard now updates
  values in place and only reloads the map when the vehicle's position actually changes (so it's
  static when parked, and refreshes just once per new GPS point while driving).

## 0.4.2

### ✨ Improvements
- Dashboard now shows the **add-on version** (header chip + footer), sourced from the Supervisor
  `BUILD_VERSION` build arg.
- Renamed the sidebar panel to **Tesla Telemetry**.

## 0.4.1

### 🐛 Bug Fixes
- **Bundled websocket server: use the HTTP-receiver variant.** The published `myteslamate/websocket`
  image is the Google Pub/Sub *pull* variant (crashes without `/key.json`). Vendor the upstream
  `master` source instead — the `POST /` receiver with no GCP dependency — and npm-install it at
  build, so the self-hosted glue path works without any cloud credentials.

## 0.4.0

### ✨ New Features
- **Bundled TeslaMate websocket server**: set `enable_teslamate_bridge` to run the
  [MyTeslaMate websocket server](https://github.com/MyTeslaMate/websocket) *inside* this add-on
  (on port 8081) right next to the telemetry receiver — no separate container. The built-in glue
  forwards decoded records to it locally, and TeslaMate streams from it. `teslamate_bridge_url`
  remains as an optional override to point at an external websocket server instead.
- The websocket server app is vendored from the upstream image (pinned by digest) and run with the
  add-on's Node runtime.

## 0.3.0

### ✨ New Features
- **TeslaMate bridge (self-hosted, no Google Pub/Sub)**: optional `teslamate_bridge_url` option.
  When set, the add-on forwards decoded telemetry records to a [MyTeslaMate websocket
  server](https://github.com/MyTeslaMate/websocket) by POSTing the same
  `{"message":{"data":base64(payload)}}` envelope that a Pub/Sub push subscription would — so
  TeslaMate can use the websocket as its streaming source without any cloud dependency. stdlib-only
  glue that tails the logger output; isolated from the telemetry path.

## 0.2.1

### 🔒 Security
- **Dashboard XSS hardening**: HTML-escape every telemetry-derived value rendered in the dashboard
  (VIN, field names/values, "Other signals", client version, namespace, cert string), and validate
  the VIN server-side against the standard 17-char format before use. Defense-in-depth — the data
  arrives over authenticated mTLS, but values are now never trusted as markup.

## 0.2.0

### ✨ New Features
- **Telemetry dashboard (ingress panel)**: a built-in web page in the HA sidebar showing the
  latest live telemetry per vehicle — battery (with charging trend + SoC sparkline), speed,
  gear, odometer, and a location mini-map — plus stream health (records/min, total records,
  last-seen, online status, TLS cert expiry, add-on uptime, telemetry client version). It also
  renders any extra telemetry fields you add to the config automatically.
- Dashboard is read-only and isolated from the telemetry path (it tails a copy of the logger
  output via `tee`), so it can never disrupt the vehicle stream.

## 0.1.2

### 🐛 Bug Fixes
- **Clearer NPM permissions error**: if the configured NPM account can see no certificates
  (non-admin users only see their own), the log now says so explicitly instead of the misleading
  "no certificate found for domain".

## 0.1.1

### 🐛 Bug Fixes
- **Case-insensitive certificate matching**: resolve the NPM certificate by domain regardless of
  the case typed in `npm_cert_domain` (Let's Encrypt/NPM store domains lowercase).

## 0.1.0

### ✨ Initial release
- Runs Tesla's `fleet-telemetry` server (pinned upstream image `tesla/fleet-telemetry:v0.9.0`,
  amd64) inside Home Assistant.
- All server options exposed on the Configuration page: log level, namespace, reliable ack, rate
  limiting, and Prometheus metrics.
- **First-class backends:** Logger (stdout), MQTT (auto-discovers the Home Assistant Mosquitto
  broker), and Google Pub/Sub (service-account JSON paste; topics auto-created).
- **`extra_config_json`** escape hatch deep-merges over the generated config for advanced backends
  (Kafka, Kinesis, ZMQ, custom `records` routing, `tls.ca_file`, …).
- **NGINX Proxy Manager TLS integration:** fetches the Let's Encrypt certificate for your
  telemetry hostname from the NPM API on startup and re-pulls every `cert_refresh_hours`,
  restarting the server only when the certificate changes.
- Supervised server process: the add-on exits (so Supervisor restarts it) if `fleet-telemetry`
  dies, and handles `SIGTERM` for clean shutdown.
- Full setup guide in `DOCS.md` covering Tesla developer setup, public-key hosting, the required
  NPM passthrough Stream, per-backend configuration, vehicle `fleet_telemetry_config`, and
  troubleshooting.
