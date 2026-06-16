# Changelog

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
