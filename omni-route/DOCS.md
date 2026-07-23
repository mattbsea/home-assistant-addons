# OmniRoute

Free AI gateway — 268+ providers, one OpenAI-compatible endpoint, auto-fallback.

**Version:** 3.8.48

## Configuration

| Option | Description | Default |
|--------|-------------|---------|
| `log_level` | Logging verbosity: `debug`, `info`, `warn`, `error` | `info` |
| `admin_password` | Dashboard login password. Leave blank to use the built-in default (`OmniRoute123!`) — change it from the dashboard after first login either way | `""` |
| `dashboard_key` | Key to access the web dashboard | `""` |
| `storage_encryption_key` | Encrypts API keys at rest (AES-256-GCM) | `""` |

## Logging

All HTTP requests are logged to both the Home Assistant add-on logs (console) and to files on disk at `/data/logs/application/app.log`. Request history is also stored in the OmniRoute database and visible in the dashboard under **Logs**.

| Setting | Value |
|---------|-------|
| Console output | Human-readable text |
| File output | `/data/logs/application/app.log` |
| Max file size | 50 MB (auto-rotated) |
| File retention | 30 days |
| Request log retention | 30 days |
| Max request log rows | 200,000 |

Set `log_level: debug` in the add-on options for verbose request details including provider, model, tokens, and latency.

## Access

OmniRoute is **not** exposed through Home Assistant's Ingress sidebar — OmniRoute (Next.js) doesn't support Ingress's prefix-stripping proxy model, so routing and authentication break when accessed that way.

Instead, reach the dashboard directly on the exposed port (`http://<home-assistant-ip>:20128/dashboard`), or put your own reverse proxy (e.g. NGINX Proxy Manager) in front of it for a domain name and TLS. The dashboard login is required — log in with the password from `admin_password` (or the built-in default `OmniRoute123!` if left blank), and change it from the dashboard afterward.

## Ports

| Port | Protocol | Description |
|------|----------|-------------|
| 20128 | TCP | Dashboard and API endpoint |

## Usage

Once running, point any OpenAI-compatible tool at:

```
http://<home-assistant-ip>:20128/v1
```

Set your model to `auto` for automatic provider fallback across free tiers.
