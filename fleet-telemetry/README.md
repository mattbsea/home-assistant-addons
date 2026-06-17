# Tesla Fleet Telemetry

Self-hosted [Tesla fleet-telemetry](https://github.com/teslamotors/fleet-telemetry) server as a
Home Assistant add-on. Vehicles stream high-frequency telemetry to your server over mTLS; records
are dispatched to a **Logger**, **MQTT**, and/or **Google Pub/Sub**.

## Features

- Runs the official `fleet-telemetry` binary (pinned `v0.9.0`, amd64).
- **Built-in setup wizard.** No Configuration page — everything is configured through a guided
  wizard that generates the EC keypair, hosts the public key, registers your Tesla partner
  account, fetches your TLS cert, and configures your vehicle. It writes one local config file
  (`/data/wizard-config.json`) and (re)starts services reactively.
- **mTLS handled by the add-on.** Automatically fetches and renews the TLS certificate from your
  NGINX Proxy Manager via its API, and auto-creates the NPM proxy host for the public key.
- First-class backends: Logger (stdout), MQTT (auto-discovers the HA Mosquitto broker), and Google
  Pub/Sub (topics auto-created).

## Quick start

1. Install the add-on and **open it from the sidebar** — the setup wizard launches automatically.
2. Have ready: a Tesla developer app (Client ID + Secret), a public domain pointing at your home
   IP, and NGINX Proxy Manager. Then follow the wizard step by step.
3. The wizard generates your signing keypair, creates the public-key proxy host in NPM, registers
   your domain with Tesla, helps you create the telemetry **Stream**, logs you in, and sends the
   `fleet_telemetry_config` to your vehicle.

> Telemetry uses **mTLS**, so the telemetry endpoint must be an NPM **TCP-passthrough Stream**, not
> an HTTP proxy host. The telemetry port is configurable, so the public key can be served by an
> ordinary HTTPS proxy host on :443 of the same domain.

See **[DOCS.md](DOCS.md)** for the full setup guide and troubleshooting.

## License

The add-on packaging is MIT. The bundled `fleet-telemetry` binary is © Tesla, Apache-2.0.
