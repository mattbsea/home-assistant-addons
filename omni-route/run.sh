#!/usr/bin/with-contenv bashio
# shellcheck shell=bash

# --- Configuration from options ---
LOG_LEVEL=$(bashio::config 'log_level')
DASHBOARD_KEY=$(bashio::config 'dashboard_key')
ENCRYPTION_KEY=$(bashio::config 'storage_encryption_key')

bashio::log.info "Starting OmniRoute..."

# --- Build environment ---
export STORAGE_DIR="/data"

# OmniRoute (a Next.js app) has no built-in Home Assistant Ingress awareness.
# Supervisor strips the `/api/hassio_ingress/<token>` prefix before forwarding
# requests to the add-on, but never rewrites response bodies/headers on the
# way back. Left alone, OmniRoute's own root-absolute redirects
# (`/` -> `/dashboard`) and asset URLs (`/_next/static/...`) escape the
# Ingress-proxied path and 404 in the Home Assistant frontend.
#
# The fix: OmniRoute listens on an internal-only port (UPSTREAM_PORT) with
# OMNIROUTE_BASE_PATH set to this add-on's stable Ingress entry path, so
# Next.js's basePath support prefixes every redirect, asset URL, and link it
# emits. ingress-proxy.js serves the real Ingress-facing port (INGRESS_PORT,
# matching config.yaml's ingress_port/webui/ports) and re-adds that same
# prefix to incoming requests before forwarding upstream — since Supervisor
# already stripped it, and direct/LAN access never had it either. See
# scripts/ingress-proxy.js for the detail.
INGRESS_PORT=20128
UPSTREAM_PORT=20130

# ingress_entry is a stable, persisted path (e.g. /api/hassio_ingress/<token>)
# assigned to this add-on installation — not a per-session value — so it's
# safe to bake into OMNIROUTE_BASE_PATH for the lifetime of the container.
INGRESS_ENTRY=$(bashio::app.ingress_entry 2>/dev/null || true)

if [ -n "$INGRESS_ENTRY" ]; then
    bashio::log.info "Ingress entry is ${INGRESS_ENTRY}; configuring OmniRoute base path."
    export OMNIROUTE_BASE_PATH="$INGRESS_ENTRY"
else
    bashio::log.warning "Could not determine Ingress entry path; sidebar dashboard access may 404."
fi

export PORT="$UPSTREAM_PORT"

# --- Logging ---
export APP_LOG_LEVEL="${LOG_LEVEL:-info}"
export APP_LOG_FORMAT=text
export APP_LOG_TO_FILE=true
export APP_LOG_FILE_PATH="/data/logs/application/app.log"
export APP_LOG_MAX_FILE_SIZE=50M
export APP_LOG_RETENTION_DAYS=30
export APP_LOG_MAX_FILES=20
export CALL_LOG_RETENTION_DAYS=30
export CALL_LOG_MAX_ENTRIES=50000
export CALL_LOGS_TABLE_MAX_ROWS=200000
export PROXY_LOGS_TABLE_MAX_ROWS=200000

if [ -n "$ENCRYPTION_KEY" ]; then
    export STORAGE_ENCRYPTION_KEY="$ENCRYPTION_KEY"
fi

if [ -n "$DASHBOARD_KEY" ]; then
    export DASHBOARD_KEY="$DASHBOARD_KEY"
fi

# Set a predictable initial password for ingress auth
export INITIAL_PASSWORD="${INITIAL_PASSWORD:-OmniRoute123!}"

# --- Start OmniRoute in background (bootstrap mode - no auth for loopback) ---
omniroute &
OMNIRoute_PID=$!

# --- Wait for OmniRoute to be ready ---
# All internal calls below target UPSTREAM_PORT directly (not through
# ingress-proxy.js), so they must include INGRESS_ENTRY themselves whenever
# OMNIROUTE_BASE_PATH is set — OmniRoute only serves routes under that prefix.
bashio::log.info "Waiting for OmniRoute to start..."
READY=false
for i in $(seq 1 30); do
    if curl -sf "http://localhost:${UPSTREAM_PORT}${INGRESS_ENTRY}/login" > /dev/null 2>&1; then
        bashio::log.info "OmniRoute is ready."
        READY=true
        break
    fi
    sleep 1
done

if [ "$READY" = false ]; then
    bashio::log.warning "OmniRoute did not become ready within 30 seconds."
fi

# --- Disable dashboard login requirement for ingress ---
bashio::log.info "Disabling dashboard login requirement for ingress..."

# Try bare PATCH first (works in bootstrap mode with no password)
PATCH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X PATCH "http://localhost:${UPSTREAM_PORT}${INGRESS_ENTRY}/api/settings" \
    -H "Content-Type: application/json" \
    -d '{"requireLogin": false}')

if [ "$PATCH_STATUS" = "200" ]; then
    bashio::log.info "Dashboard login disabled (bootstrap mode)."
else
    bashio::log.info "Bootstrap PATCH returned ${PATCH_STATUS}, trying authenticated mode..."

    # Use INITIAL_PASSWORD env var or default
    AUTH_PW="${INITIAL_PASSWORD:-OmniRoute123!}"

    # Login to get auth token cookie
    curl -s -c /tmp/or_cookies -X POST "http://localhost:${UPSTREAM_PORT}${INGRESS_ENTRY}/api/auth/login" \
        -H "Content-Type: application/json" \
        -d "{\"email\":\"admin@localhost\",\"password\":\"${AUTH_PW}\"}" > /dev/null 2>&1

    # PATCH with auth cookie and currentPassword
    PATCH2=$(curl -s -b /tmp/or_cookies -o /dev/null -w "%{http_code}" -X PATCH "http://localhost:${UPSTREAM_PORT}${INGRESS_ENTRY}/api/settings" \
        -H "Content-Type: application/json" \
        -d "{\"requireLogin\": false, \"currentPassword\": \"${AUTH_PW}\"}")

    if [ "$PATCH2" = "200" ]; then
        bashio::log.info "Dashboard login disabled (authenticated mode)."
    else
        bashio::log.warning "Could not disable dashboard login (PATCH returned ${PATCH2}). Ingress may require manual configuration."
    fi
    rm -f /tmp/or_cookies
fi

# --- Start the ingress-facing proxy ---
# NODE_PATH lets ingress-proxy.js's require("http-proxy") find the package
# installed globally by the Dockerfile.
bashio::log.info "Starting ingress proxy on port ${INGRESS_PORT}..."
export INGRESS_LISTEN_PORT="$INGRESS_PORT"
export INGRESS_UPSTREAM_PORT="$UPSTREAM_PORT"
export INGRESS_ENTRY
NODE_PATH="$(npm root -g)" node /opt/scripts/ingress-proxy.js &
INGRESS_PROXY_PID=$!

# --- Forward signals and wait for both processes ---
cleanup() {
    bashio::log.info "Shutting down OmniRoute..."
    kill -TERM "$OMNIRoute_PID" "$INGRESS_PROXY_PID" 2>/dev/null
    wait "$OMNIRoute_PID" "$INGRESS_PROXY_PID" 2>/dev/null
    exit 0
}
trap cleanup SIGTERM SIGINT

wait -n "$OMNIRoute_PID" "$INGRESS_PROXY_PID"
cleanup
