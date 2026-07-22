---
name: ha-addon-authoring
description: Expert guidance for building, testing, and publishing Home Assistant Supervisor add-ons
---

# Home Assistant Add-on Authoring Skill

Expert guidance for building, testing, and publishing Home Assistant Supervisor add-ons.

## When to Use

- Creating a new add-on (config.yaml, Dockerfile, run.sh, build.yaml)
- Updating or debugging an existing add-on
- Publishing add-on updates (version bumps, repository pushes)
- Debugging add-on builds, installs, or runtime issues
- Configuring ingress, ports, networking, or Supervisor API access

## Core Files per Add-on

Every add-on requires these files in its own directory:

```
my_addon/
├── config.yaml      # Add-on metadata (name, slug, version, arch, options)
├── Dockerfile       # Container build instructions
├── run.sh           # Entrypoint script (#!/usr/bin/with-contenv bashio)
├── build.yaml       # Multi-architecture build targets
├── DOCS.md          # Documentation (shown in add-on store)
├── CHANGELOG.md     # Version history
└── README.md        # Optional extra docs
```

## config.yaml Reference

```yaml
name: "My Add-on"
slug: "my_addon"
version: "1.0.0"           # MUST bump on every change
arch:
  - amd64
  - aarch64
  - armv7
startup: application        # application | services | initialize | system
boot: auto                  # auto | manual
watchdog: http://[HOST]:8080/healthz  # Optional health check URL

# API access
hassio_api: true            # Access Supervisor REST API (http://supervisor/)
homeassistant_api: true     # Access HA REST API (http://supervisor/core/api/)

# Ingress (sidebar panel)
ingress: true
ingress_port: 8080
ingress_stream: true        # Stream responses (needed for SSE/large payloads)
panel_icon: mdi:route       # Sidebar icon
panel_title: "My Add-on"    # Sidebar label
webui: http://[HOST]:[PORT:8080]/  # URL opened from sidebar click

# Ports
ports:
  "8080/tcp": 8080          # Exposed externally
  "8081/tcp": null          # null = ingress-only, not exposed

# Storage mapping
map:
  - data:rw                 # /data — persistent add-on data
  - config:ro               # /config — read-only HA config
  - share:rw                # /share — shared storage
  - ssl:rw                  # /ssl — SSL certificates

# Options and schema
options:
  my_setting: "default"
  api_key: ""
schema:
  my_setting: str
  api_key: str

# Privileges (minimal principle)
privileged:
  - NET_ADMIN               # Only if truly needed
```

## Dockerfile Best Practices

```dockerfile
ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install dependencies in a single layer
RUN \
    apk add --no-cache \
        python3 \
        py3-pip \
        curl \
    && pip3 install --break-system-packages \
        httpx

# Copy app source
COPY / /app/

# Set working directory
WORKDIR /app

# Use S6 overlay for process management (standard in HA add-ons)
CMD ["/app/run.sh"]
```

Key rules:
- Always use `ARG BUILD_FROM` + `FROM ${BUILD_FROM}` — the build system provides the base image
- Prefer Alpine-based images (smaller, faster builds)
- Single `RUN` layer for dependencies (smaller image)
- Never hardcode `FROM` — always use the build arg
- Base images: `ghcr.io/hassio-addons/base:latest` for general, or specific language images

## run.sh Pattern

```bash
#!/usr/bin/with-contenv bashio
# shellcheck shell=bash

# --- Configuration from options ---
MY_SETTING=$(bashio::config 'my_setting')
API_KEY=$(bashio::config 'api_key')

bashio::log.info "Starting My Add-on..."

# --- Supervisor API calls ---
# Get add-on info
ADDON_INFO=$(bashio::api.supervisor "/addons/self/options")

# --- Home Assistant API calls ---
# Get all entities
ENTITIES=$(bashio::api.core "/states")

# --- Webserver / app launch ---
exec python3 /app/server.py \
    --setting "$MY_SETTING" \
    --api-key "$API_KEY"
```

Key rules:
- Always use `#!/usr/bin/with-contenv bashio` as the shebang
- Use `bashio::config` to read options from config.yaml
- Use `bashio::log.info` / `bashio::log.warning` / `bashio::log.error` for logging
- Use `bashio::api.supervisor` and `bashio::api.core` for API calls
- Use `exec` for the final process (replaces shell, proper signal handling)
- Use `bashio::var.is_empty` and `bashio::var.has_value` for conditionals

## build.yaml Reference

```yaml
build_from:
  aarch64: ghcr.io/hassio-addons/base:latest
  amd64: ghcr.io/hassio-addons/base:latest
  armv7: ghcr.io/hassio-addons/base:latest
  i386: ghcr.io/hassio-addons/base:latest

codenotary:
  signer: notary@example.com
  # Optional: notary_server: https://notary.example.com
```

## Calling HA API from Add-on

### Via Supervisor proxy (recommended)

```python
import os, httpx

HA_URL = "http://supervisor/core/api"
TOKEN = os.environ["SUPERVISOR_TOKEN"]
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

async def get_states():
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{HA_URL}/states", headers=HEADERS)
        return r.json()

async def call_service(domain, service, data):
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{HA_URL}/services/{domain}/{service}",
            headers=HEADERS, json=data
        )
        return r.json()
```

### Via bash (in run.sh)

```bash
# Get all entities
ENTITIES=$(bashio::api.core "/states")

# Call a service
bashio::api.core "/services/light/turn_on" POST '{"entity_id": "light.living_room"}'

# Get add-on config
OPTIONS=$(bashio::api.supervisor "/addons/self/options")
```

## Ingress Setup

Ingress lets users access your add-on through the HA sidebar without exposing ports.

config.yaml:
```yaml
ingress: true
ingress_port: 8080
ingress_stream: true        # Required for SSE, WebSocket, or streaming responses
webui: http://[HOST]:[PORT:8080]/
```

Your app must:
1. Listen on `0.0.0.0:8080` inside the container
2. Accept connections from any origin (ingress proxy forwards requests)
3. Set `X-Frame-Options: SAMEORIGIN` if serving HTML (HA frames it)

The `[HOST]` and `[PORT:8080]` tokens in `webui` are replaced by HA at runtime:
- `[HOST]` → the HA host IP
- `[PORT:8080]` → the mapped host port

## Supervisor API Reference

Base URL: `http://supervisor` (from inside the container)

### Common endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/addons` | GET | List all add-ons |
| `/addons/self` | GET | Current add-on info |
| `/addons/self/options` | GET/POST | Current add-on config |
| `/addons/self/update` | POST | Trigger self-update |
| `/core/api/states` | GET | All HA entity states |
| `/core/api/services` | GET | All HA services |
| `/core/api/services/{domain}/{service}` | POST | Call a service |
| `/core/api/config` | GET | HA core config |
| `/core/api/history/period/{entity}` | GET | Entity history |
| `/core/api/states/{entity}` | GET | Single entity state |
| `/core/websocket` | WS | WebSocket API |

### Authentication

All requests require the `SUPERVISOR_TOKEN` environment variable:

```bash
Authorization: Bearer ${SUPERVISOR_TOKEN}
```

## Publishing Updates

### Step 1: Bump version in config.yaml
```yaml
version: "1.0.1"  # MUST change or Supervisor won't see the update
```

### Step 2: Commit and push
```bash
git add my_addon/
git commit -m "fix: description of change"
git push origin main
```

### Step 3: Trigger HA to check for updates
- User goes to **Settings → Add-ons → ⋮ → Check for updates**
- Or wait for Supervisor's periodic repo refresh (~1 hour)

### Step 4: Install update
- User clicks **Update** in the add-on page

### NEVER
- Skip the version bump — the Supervisor caches versions
- Push without verifying the commit includes actual file changes
- Restart Home Assistant without explicit user permission

## Debugging

### Check add-on logs
```bash
ha_get_logs(source="supervisor", slug="my_addon_slug", limit=50)
```

### Check add-on status
```bash
ha_get_addon(slug="my_addon_slug")
```

### Key fields to watch
- `state`: started / stopped / error
- `version` vs `version_latest`: must match after update
- `update_available`: true means new version is in the repo
- `ingress_panel`: true means sidebar panel is active

### Common issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| Add-on won't install | Missing arch in config.yaml | Add `amd64` / `aarch64` |
| Ingress 404 | Wrong webui URL | Check `webui:` matches app port |
| Build fails | Bad base image | Use `ghcr.io/hassio-addons/base:latest` |
| No sidebar panel | Missing ingress/panel_icon | Add `ingress: true` + `panel_icon` |
| API 401 | Missing hassio_api | Set `hassio_api: true` in config.yaml |
| Container won't start | Bad shebang in run.sh | Use `#!/usr/bin/with-contenv bashio` |
| Options not showing | Missing schema | Add `schema:` matching `options:` |

## Repository Structure

```
my-addon-repo/
├── repository.yaml           # Repo metadata (name, description, url)
└── my_addon/
    ├── config.yaml
    ├── Dockerfile
    ├── run.sh
    ├── build.yaml
    └── src/
        └── app.py
```

### repository.yaml
```yaml
name: "My Add-on Repository"
description: "Custom add-ons for Home Assistant"
url: "https://github.com/you/ha-addons"
maintainer: "You <you@example.com>"
```

## Security Best Practices

1. **Minimal privileges** — never add `privileged` unless absolutely necessary
2. **No secrets in config.yaml** — use `options` + `schema` for user-provided secrets
3. **Read-only where possible** — use `config:ro` instead of `config:rw`
4. **Validate input** — always validate user options before using them
5. **HTTPS for external ports** — if exposing ports, support SSL
6. **SUPERVISOR_TOKEN** — never log or expose it; it's available as an env var
7. **User isolation** — run processes as non-root when possible
8. **Network isolation** — don't bind to 0.0.0.0 unless needed; ingress handles routing

## Multi-Architecture Builds

Home Assistant supports multiple architectures. Your add-on must build for all target archs:

```yaml
# config.yaml
arch:
  - amd64
  - aarch64
  - armv7
```

```yaml
# build.yaml
build_from:
  amd64: ghcr.io/hassio-addons/base:latest
  aarch64: ghcr.io/hassio-addons/base:latest
  armv7: ghcr.io/hassio-addons/base:latest
```

Use the official HA builder or GitHub Actions to build for all architectures.
