# Tesla Fleet Telemetry

Run Tesla's [`fleet-telemetry`](https://github.com/teslamotors/fleet-telemetry) server inside
Home Assistant. Vehicles stream high-frequency telemetry directly to your server over an
encrypted, mutually-authenticated WebSocket; the server fans those records out to a **Logger**
(stdout), an **MQTT** broker, and/or **Google Pub/Sub**.

This is the *server* side only. Decoding the records into Home Assistant entities (or feeding
TeslaMate) is a separate consumer that reads from MQTT/Pub/Sub.

---

## How it works (read this first)

```
Tesla vehicle ──WSS / mTLS──▶ NPM Stream (TCP passthrough :443) ──TCP──▶ HA host:<mapped port>
                                                                              │
                                                                  fleet-telemetry add-on
                                                                  (terminates TLS + mTLS itself)
                                                                              │
                                          ┌───────────────────┬───────────────┴───────────────┐
                                     logger (stdout)       MQTT broker                  Google Pub/Sub
```

> **⚠️ The server is mTLS-only and terminates TLS itself.**
> fleet-telemetry refuses to start without a TLS certificate and *requires and verifies* the
> vehicle's client certificate. Therefore your public reverse proxy **must be a Layer-4 TCP
> passthrough** (in NGINX Proxy Manager: a **Stream**, not a Proxy Host). The proxy must **not**
> terminate TLS — if it did, the vehicle's client-certificate identity would be lost and the
> handshake would fail. The TLS certificate for your public hostname therefore lives **with this
> add-on**, which fetches it from NPM's API (see below).
>
> You do **not** need to manage the vehicle's client CA — Tesla's production CA is built into the
> server.

---

## Step 1 — Tesla developer setup (API keys)

You need a Tesla developer application and a paired virtual key before any vehicle will connect.

1. **Create an application** at <https://developer.tesla.com>. Request the
   `vehicle_device_data` scope (and any others you need). Note your **Client ID** and
   **Client Secret**.
2. **Generate an EC key pair** (secp256r1 / prime256v1) for your application:
   ```bash
   openssl ecparam -name prime256v1 -genkey -noout -out private-key.pem
   openssl ec -in private-key.pem -pubout -out public-key.pem
   ```
3. **Host the public key** at exactly this path on your registered domain:
   ```
   https://<your-domain>/.well-known/appspecific/com.tesla.3p.public-key.pem
   ```
   > This static file is **not** served by this add-on. Host it with your existing web stack —
   > e.g. an NGINX Proxy Manager *Proxy Host* / custom location, or any static host. The domain
   > here is your **application** domain; it can differ from the telemetry hostname in Step 2.
4. **Get a Partner Authentication token** and **register** your partner account against the
   Fleet API `partner_accounts` endpoint (this is where Tesla fetches the public key above).
5. **Pair the virtual key** to each vehicle (the owner approves the app's virtual key in the
   Tesla mobile app — commonly via a `https://tesla.com/_ak/<your-domain>` deep link). Follow the
   exact current flow in Tesla's docs, as the pairing URL/steps change over time.

Full reference: <https://developer.tesla.com/docs/fleet-api> and the
[fleet-telemetry README](https://github.com/teslamotors/fleet-telemetry#getting-started).

---

## Step 2 — Public hostname, certificate, and the NPM Stream

Pick a public hostname for telemetry, e.g. `telemetry.example.org`.

1. **Issue a Let's Encrypt certificate** for that hostname in NGINX Proxy Manager
   (*SSL Certificates → Add → Let's Encrypt*). A **DNS challenge** works even though nothing is
   served over HTTP on this host.
2. **Create a Stream** (*Hosts → Streams → Add Stream*):
   - **Incoming Port:** `443` (the public port vehicles connect to).
   - **Forward Host:** your Home Assistant host IP.
   - **Forward Port:** the host port mapped to the add-on's `4443` (see the add-on **Network**
     tab; default `4443`).
   - **TCP Forwarding:** on. **UDP:** off. **Do not** attach SSL termination — leave it as raw
     passthrough.
3. **Point DNS** for `telemetry.example.org` at your public IP and forward TCP `443` to NPM.

The add-on pulls the certificate's PEMs from NPM automatically — see the `npm_*` options below.

---

## Step 3 — Add-on configuration

Open the add-on **Configuration** tab. Start with the **Logger** backend so you can confirm
vehicles connect, then add MQTT and/or Pub/Sub.

### Server options

| Option | Default | Description |
|--------|---------|-------------|
| `log_level` | `info` | `trace`, `debug`, `info`, `warn`, or `error`. |
| `json_log_enable` | `true` | Emit structured JSON logs. |
| `namespace` | `tesla_telemetry` | Topic/stream prefix used by the dispatchers. |
| `reliable_ack` | `false` | Only ack the vehicle after the backend confirms receipt (recommend `true` with Kafka/Pub/Sub for at-least-once delivery). |
| `rate_limit_enabled` | `true` | Enable per-vehicle message rate limiting. |
| `rate_limit_message_interval` | `30` | Rate-limit window, seconds. |
| `rate_limit_message_limit` | `1000` | Max messages per window. |
| `metrics_enabled` | `false` | Expose Prometheus metrics on port `9090`. |

### TLS / NGINX Proxy Manager (required)

| Option | Description |
|--------|-------------|
| `npm_url` | NPM admin API base URL, e.g. `https://proxy.example.org:81`. |
| `npm_email` / `npm_password` | NPM login used to read the certificate via the API. |
| `npm_cert_domain` | The certificate's domain, e.g. `telemetry.example.org`. |
| `cert_refresh_hours` | How often (hours) to re-pull the cert from NPM so renewals are picked up. The add-on restarts the server only when the cert actually changes. |

### Backends

At least one backend must be enabled; if none are, the add-on falls back to the Logger.

**Logger** — `enable_logger: true`. Records are serialized to JSON in the add-on log. Zero setup;
ideal for verifying connectivity.

**MQTT** — `enable_mqtt: true`.

| Option | Description |
|--------|-------------|
| `mqtt_broker` | `host:port`. **Leave blank** to auto-discover the Home Assistant Mosquitto broker. |
| `mqtt_client_id` | Client id (default `fleet-telemetry`). |
| `mqtt_topic_base` | Root topic; messages publish under `<topic_base>/...`. |
| `mqtt_qos` | 0, 1, or 2. |
| `mqtt_username` / `mqtt_password` | Optional; auto-filled from the discovered HA broker. |

**Google Pub/Sub** — `enable_pubsub: true`.

| Option | Description |
|--------|-------------|
| `gcp_project_id` | Your GCP project id. |
| `gcp_service_account_json` | Paste the full service-account JSON key. It is written to `/data/gcp-credentials.json` (mode 600) and exported as `GOOGLE_APPLICATION_CREDENTIALS`. |

The service account needs the **Pub/Sub Editor** role (`roles/pubsub.editor`) — the server
**auto-creates** the topics (`<namespace>_V`, `<namespace>_connectivity`, …) on startup and
panics if it cannot.

### Advanced — raw config escape hatch

`extra_config_json` accepts a JSON object that is **deep-merged over** the generated config. Use
it for backends not exposed above (Kafka, Kinesis, ZMQ, MQTT TLS, statsd, custom `records`
routing, `tls.ca_file`, etc.). Example to add Kafka and route `V` to both logger and Kafka:

```json
{
  "kafka": { "bootstrap.servers": "kafka:9092", "queue.buffering.max.messages": 1000000 },
  "records": { "V": ["logger", "kafka"] }
}
```

> Use the Configuration tab's **YAML mode** to paste multi-line values like
> `gcp_service_account_json` and `extra_config_json`.

---

## Step 4 — Point your vehicles at the server

Tell each vehicle where to stream, using the Fleet API
`api/1/vehicles/fleet_telemetry_config` endpoint. The `config` object references your telemetry
hostname, the CA the vehicle should trust (the Let's Encrypt chain), and the fields to stream.
The shape below is **illustrative** — confirm the exact field names and value schema against the
current [Fleet API telemetry docs](https://developer.tesla.com/docs/fleet-api) and the
[fleet-telemetry protos](https://github.com/teslamotors/fleet-telemetry/tree/main/protos), which
change over time:

```jsonc
{
  "vins": ["<your-vin>"],
  "config": {
    "hostname": "telemetry.example.org",
    "port": 443,
    "ca": "-----BEGIN CERTIFICATE-----\n...ISRG/LE chain...\n-----END CERTIFICATE-----",
    "fields": {
      "VehicleSpeed": { "interval_seconds": 10 },
      "Soc":          { "interval_seconds": 60 },
      "Location":     { "interval_seconds": 10 }
    }
  }
}
```

Vehicles require firmware **2023.20.6 or later**. After the config is accepted, the car opens a
WebSocket to `telemetry.example.org:443`, which NPM passes through to the add-on.

---

## Verification

1. **Add-on log** — with the Logger backend you should see incoming records as JSON once a car
   connects. `connectivity` records mark a vehicle going online/offline.
2. **Status endpoint** — the server exposes a health check on the status port (`8080` internally;
   see the add-on Network tab for the mapped host port). The exact path (e.g. `/status`) depends
   on the upstream version.
3. **Metrics** — if `metrics_enabled`, Prometheus metrics are on port `9090`.
4. **MQTT** — subscribe to `<mqtt_topic_base>/#` on your broker and watch records arrive.
5. **Pub/Sub** — confirm the `<namespace>_V` topic was created and is receiving messages.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| Add-on exits at startup with *"could not fetch a certificate from NPM"* | Wrong `npm_url`/credentials, or `npm_cert_domain` doesn't match a cert in NPM. The URL must reach the NPM **admin** API (often port `81`). |
| *"tls config is empty - telemetry server is mTLS only"* | The cert wasn't written. Confirm `/data/certs/server.crt` exists; re-check the `npm_*` options. |
| Vehicle never connects / TLS handshake errors | The proxy is terminating TLS. It must be a **Stream / TCP passthrough**, not a Proxy Host. Also verify DNS and the public `443` forward. |
| Vehicle connects then drops | Cert hostname mismatch with `hostname` in the vehicle config, or the `ca` you sent the car doesn't match the served chain. |
| Pub/Sub panic on startup | Service account lacks `roles/pubsub.editor`, or `gcp_project_id` is wrong. |
| No data but vehicle paired | Firmware < 2023.20.6, virtual key not paired, or `fields` not configured in `fleet_telemetry_config`. |

To suppress noisy TLS handshake error logs (e.g. from internet scanners hitting the port), add
`SUPPRESS_TLS_HANDSHAKE_ERROR_LOGGING=true` — currently only via a custom build; most users can
ignore the noise.
