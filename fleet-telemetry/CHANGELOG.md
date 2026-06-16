# Changelog

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
