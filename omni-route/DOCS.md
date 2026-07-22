# OmniRoute

Free AI gateway — 268+ providers, one OpenAI-compatible endpoint, auto-fallback.

**Version:** 3.8.48

## Configuration

| Option | Description | Default |
|--------|-------------|---------|
| `log_level` | Logging verbosity: `debug`, `info`, `warn`, `error` | `info` |
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

## Ingress

Access the OmniRoute dashboard from the Home Assistant sidebar after installation. The dashboard login requirement is automatically disabled to allow seamless access through the Home Assistant ingress proxy.

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
