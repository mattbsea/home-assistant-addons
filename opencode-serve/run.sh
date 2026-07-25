#!/usr/bin/env bash

echo "=== OpenCode HA Add-on Starting ==="

# ---------------------------------------------------------------------------
# Source s6 container environment
# ---------------------------------------------------------------------------
if [ -d /run/s6/container_environment ]; then
    for f in /run/s6/container_environment/*; do
        export "$(basename "$f")=$(cat "$f")"
    done
fi
export SUPERVISOR_TOKEN="${SUPERVISOR_TOKEN:-$HASSIO_TOKEN}"

# ---------------------------------------------------------------------------
# 1. Read add-on options
# ---------------------------------------------------------------------------
OPTIONS="/data/options.json"

LOG_LEVEL=$(jq -r '.log_level // "info"' "$OPTIONS")

echo "Log level: ${LOG_LEVEL}"

# ---------------------------------------------------------------------------
# 2. Set up persistent directories
# ---------------------------------------------------------------------------
export XDG_STATE_HOME="/data/state"
mkdir -p "$XDG_STATE_HOME"
mkdir -p /root/.config/opencode

# ---------------------------------------------------------------------------
# 3. Discover ingress entry and generate nginx config
# ---------------------------------------------------------------------------
echo "Discovering ingress path..."

INGRESS_ENTRY=""
for i in $(seq 1 30); do
    INGRESS_ENTRY=$(curl -s \
        -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
        http://supervisor/addons/self/info \
        | jq -r '.data.ingress_entry // empty' 2>/dev/null)
    if [ -n "$INGRESS_ENTRY" ]; then
        break
    fi
    echo "  Waiting for Supervisor API... (attempt $i)"
    sleep 2
done

if [ -z "$INGRESS_ENTRY" ]; then
    echo "ERROR: Could not discover ingress entry from Supervisor API"
    INGRESS_ENTRY=""
fi

echo "Ingress entry: ${INGRESS_ENTRY}"

sed "s|__INGRESS_ENTRY__|${INGRESS_ENTRY}|g" \
    /etc/nginx/nginx.conf.template \
    > /etc/nginx/http.d/opencode.conf

echo "nginx config generated"

# ---------------------------------------------------------------------------
# 4. Start nginx + OpenCode
# ---------------------------------------------------------------------------
nginx
echo "nginx listening on port 8099 (ingress)"

OPENCODE_VERSION=$(opencode --version 2>/dev/null || echo "unknown")
echo "OpenCode version: ${OPENCODE_VERSION}"
echo "Starting OpenCode server on 127.0.0.1:19876..."
echo "Sessions persist at: ${XDG_STATE_HOME}/opencode/"

exec opencode serve \
    --hostname 127.0.0.1 \
    --port 19876 \
    --log-level "${LOG_LEVEL^^}"
