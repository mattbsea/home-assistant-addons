# Tesla Fleet Telemetry

Self-hosted [Tesla fleet-telemetry](https://github.com/teslamotors/fleet-telemetry) server as a
Home Assistant add-on. Vehicles stream high-frequency telemetry to your server over mTLS; records
are dispatched to a **Logger**, **MQTT**, and/or **Google Pub/Sub**.

## Features

- Runs the official `fleet-telemetry` binary (pinned `v0.9.0`, amd64).
- All server settings exposed on the add-on Configuration page, plus a raw-JSON escape hatch for
  advanced backends (Kafka, Kinesis, ZMQ, …).
- **mTLS handled by the add-on.** Automatically fetches and renews the TLS certificate from your
  NGINX Proxy Manager via its API.
- First-class backends: Logger (stdout), MQTT (auto-discovers the HA Mosquitto broker), and Google
  Pub/Sub (topics auto-created).

## Quick start

1. Install the add-on and open **Configuration**.
2. Set `npm_url`, `npm_email`, `npm_password`, and `npm_cert_domain` so the add-on can pull your
   Let's Encrypt cert from NGINX Proxy Manager.
3. Leave the Logger backend on, start the add-on, and watch the log.
4. In NPM, create a **Stream** (TCP passthrough) from public `:443` to the add-on's mapped port.
5. Point your vehicles at the server with the Fleet API `fleet_telemetry_config` endpoint.

> The reverse proxy must be a **TCP passthrough Stream**, not an HTTP proxy host — the server is
> mTLS-only and terminates TLS itself.

See **[DOCS.md](DOCS.md)** for the full setup guide (Tesla API keys, public-key hosting, Pub/Sub,
vehicle configuration, verification, and troubleshooting).

## License

The add-on packaging is MIT. The bundled `fleet-telemetry` binary is © Tesla, Apache-2.0.
