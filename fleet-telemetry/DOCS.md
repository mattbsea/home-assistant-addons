# Tesla Fleet Telemetry

Run Tesla's [`fleet-telemetry`](https://github.com/teslamotors/fleet-telemetry) server inside
Home Assistant. Vehicles stream high-frequency telemetry directly to your server over an
encrypted, mutually-authenticated WebSocket; the server fans those records out to a **Logger**
(stdout), an **MQTT** broker, and/or **Google Pub/Sub**.

**This add-on has no Configuration page.** Everything is configured through the built-in **setup
wizard** — open the add-on from the Home Assistant sidebar and the wizard launches automatically.
It generates your signing keypair, hosts the public key, registers your Tesla partner account,
fetches your TLS certificate, and sends the streaming config to your vehicle. All settings are
written to a single local file, `/data/wizard-config.json`.

---

## How it works (read this first)

```
                      ┌─ HTTPS :443  ─ NPM Proxy Host ─▶ add-on :8100  (serves the public key)
public domain ────────┤
(e.g. fleet.x.org)    └─ TCP  :<port> ─ NPM Stream ────▶ add-on :4443  (mTLS telemetry)

Tesla vehicle ──WSS / mTLS──▶ NPM Stream (TCP passthrough) ──▶ fleet-telemetry add-on
                                                                  (terminates TLS + mTLS itself)
                                          ┌───────────────────┬───────────────┴──────────────┐
                                     logger (stdout)       MQTT broker                 Google Pub/Sub
```

> **⚠️ Telemetry is mTLS-only and the server terminates TLS itself.**
> Your telemetry endpoint **must be a Layer-4 TCP passthrough** — in NGINX Proxy Manager a
> **Stream**, not a Proxy Host. A proxy that terminated TLS would lose the vehicle's
> client-certificate identity and the handshake would fail. The TLS certificate therefore lives
> **with this add-on**, which fetches it from NPM's API.
>
> Because the **telemetry port is configurable** (it need not be 443), the *same domain* can serve
> both: an ordinary HTTPS **Proxy Host on :443** for the public key, and a **Stream** on your
> chosen telemetry port. You may also use two separate sub-domains.
>
> You do **not** manage the vehicle's client CA — Tesla's production CA is built into the server.

---

## Before you start

- A **Tesla developer app** at <https://developer.tesla.com> with the `vehicle_device_data` and
  `vehicle_location` scopes. Note its **Client ID** and **Client Secret**. Register a **redirect
  URI** (e.g. `https://<your-domain>/callback`) for the login step.
- A **public domain** pointing at your home IP (e.g. `fleet.example.org`).
- **NGINX Proxy Manager** running, reachable on its admin API, able to issue Let's Encrypt certs.
- **Router port-forwards** to NPM: `443` (public key + LE challenge) and your chosen **telemetry
  port** (default `4443`).

The wizard does the rest — you don't run any `openssl` or `curl` commands by hand.

---

## The wizard, step by step

1. **Welcome** — choose new setup or TeslaMate migration.
2. **Prerequisites** — the checklist above.
3. **Tesla app** — paste Client ID + Client Secret, pick your region (NA/EU/CN).
4. **NGINX Proxy Manager** — admin URL, email, password, and the **Home Assistant host IP** that
   NPM forwards to (the add-on can't detect this itself — use the same IP your Stream targets).
5. **Generate signing key** — the add-on creates the EC key pair (`/data/keys/`). The private key
   never leaves the add-on.
6. **Public-key domain** — enter your domain; the add-on **auto-creates an NPM proxy host**
   (HTTPS :443 + Let's Encrypt) that serves the public key at the `.well-known` path. DNS for the
   domain must already point at NPM for the certificate challenge to succeed.
7. **Verify public key** — confirms the key is reachable from the internet.
8. **Register partner** — the add-on registers your domain with Tesla automatically.
9. **Telemetry stream** — confirm the telemetry domain + port, then create the NPM **Stream**
   (TCP passthrough → add-on `:4443`). Do **not** enable SSL termination on the Stream.
10. **Verify certificate** — the add-on fetches the telemetry cert from NPM; confirm it loaded.
11. **Tesla account login** — generate the login link, sign in, and paste back the redirect URL
    (containing `?code=…`). The add-on exchanges it for a refresh token, stored locally.
12. **Backends & tuning** — pick Logger / MQTT / Pub/Sub and adjust log level, namespace, etc.
13. **Configure your vehicle** — sends the signed `fleet_telemetry_config` to your car(s).
14. **TeslaMate** (TeslaMate paths only) — choose the shim or the streaming bridge.
15. **Verification** — polls the cert and incoming records until your car starts streaming.
16. **Done** — summary.

The wizard writes each step's settings to `/data/wizard-config.json` immediately. The add-on
watches that file and (re)starts the telemetry server, shim and bridge automatically — you never
need to restart the add-on.

---

## Backends

At least one backend is always active; if none are selected the Logger is used.

- **Logger** — records serialized to the add-on log. Powers the dashboard and the TeslaMate shim,
  so keep it on if you want those.
- **MQTT** — leave the broker blank to auto-discover the Home Assistant Mosquitto broker.
- **Google Pub/Sub** — paste the service-account JSON (stored at `/data/gcp-credentials.json`,
  mode 600). The account needs **Pub/Sub Editor** (`roles/pubsub.editor`); topics
  (`<namespace>_V`, `<namespace>_connectivity`, …) are auto-created on startup.

**Advanced escape hatch:** the *Backends & tuning* step's `extra_config_json` is deep-merged over
the generated `fleet-telemetry` config, for backends not exposed in the UI (Kafka, Kinesis, ZMQ,
custom `records` routing, `tls.ca_file`, …).

---

## Feeding TeslaMate (self-hosted, no Google Pub/Sub)

The add-on can feed TeslaMate without Google Pub/Sub:

- **Fleet-API shim** — point TeslaMate at `http://<ha-host>:8085` (`TESLA_API_HOST`) and set
  `use_streaming_api = false` per car. Vehicle data is assembled from the live telemetry stream.
- **Streaming bridge** — enable the bundled websocket server (port `8081`) in the TeslaMate step,
  or set an external websocket URL. Then point TeslaMate at it
  (`TESLA_WSS_HOST=wss://<your-ws-domain>`, `TESLA_WSS_USE_VIN=true`).

---

## Dashboard

The add-on adds a **Tesla Telemetry** panel to the sidebar (ingress). It shows, per vehicle, the
latest battery/charging, speed, gear, odometer and a location mini-map, plus stream health
(records/min, total, last-seen, TLS expiry, uptime). It's read-only and isolated from the
telemetry path. The Logger backend feeds it, so keep Logger on for a populated panel.

---

## Where things are stored

| Path | Contents |
|------|----------|
| `/data/wizard-config.json` | All settings (mode 600; holds secrets). The source of truth. |
| `/data/wizard-state.json` | Wizard UI progress only. |
| `/data/keys/private-key.pem` / `public-key.pem` | The generated EC keypair (private key mode 600). |
| `/data/certs/server.{crt,key}` | TLS cert fetched from NPM. |
| `/data/config.json` | The native `fleet-telemetry` config, regenerated from the settings file. |
| `/data/shim-state.json` | TeslaMate shim snapshot. |

---

## Verification

1. **Add-on log** — with Logger on you should see incoming records as JSON once a car connects.
2. **Dashboard** — vehicles and a rising record count appear within a few minutes of the car waking.
3. **MQTT** — subscribe to `<topic_base>/#`; **Pub/Sub** — confirm the `<namespace>_V` topic fills.
4. **Metrics** — if enabled, Prometheus metrics are on port `9090`.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| Wizard says the certificate request failed at step 6 | DNS for the domain isn't pointing at NPM yet, or ports 80/443 don't reach NPM. The Let's Encrypt HTTP-01 challenge needs both. |
| Step 4 / cert fetch: *"the NPM account can see 0 certificates"* | The NPM login is a non-admin user; it only sees certs it owns. Use an NPM administrator account. |
| Step 8 partner registration fails | Wrong Client ID/Secret, wrong region, or the public key isn't reachable yet (do step 7 first). |
| Step 11 login fails | The redirect URI you used isn't registered in your Tesla app, or the pasted URL didn't contain a `code`. |
| Vehicle never connects / TLS handshake errors | The telemetry endpoint is a Proxy Host (terminating TLS) instead of a **Stream**. Also verify DNS and the telemetry-port forward. |
| Step 13 says "No VINs in shim state" | Wait a minute after the server starts so the shim can prime from the Fleet API, then retry. |
| Telemetry server "deferred until a certificate is available" in the log | Finish the NPM cert steps; once a cert is fetched the server starts automatically (no add-on restart needed). |
| Pub/Sub panic on startup | Service account lacks `roles/pubsub.editor`, or the project ID is wrong. |

The setup wizard is always reachable from the **Setup Guide** link in the dashboard header if you
need to revisit a step.
