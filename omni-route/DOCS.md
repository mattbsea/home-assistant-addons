# OmniRoute

Free AI gateway — 268+ providers, one OpenAI-compatible endpoint, auto-fallback.

**Version:** 3.8.48

## Configuration

| Option | Description | Default |
|--------|-------------|---------|
| `dashboard_key` | Key to access the web dashboard | `""` |
| `storage_encryption_key` | Encrypts API keys at rest (AES-256-GCM) | `""` |

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
