#!/usr/bin/with-contenv bashio
# shellcheck shell=bash

# --- Configuration from options ---
LOG_LEVEL=$(bashio::config 'log_level')
DASHBOARD_KEY=$(bashio::config 'dashboard_key')
ENCRYPTION_KEY=$(bashio::config 'storage_encryption_key')

bashio::log.info "Starting OmniRoute..."

# --- Build environment ---
export STORAGE_DIR="/data"
export SERVER_PORT=20128

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

# --- Start OmniRoute in background (bootstrap mode - no auth for loopback) ---
omniroute &
OMNIRoute_PID=$!

# --- Wait for OmniRoute to be ready ---
bashio::log.info "Waiting for OmniRoute to start..."
READY=false
for i in $(seq 1 30); do
    if curl -sf http://localhost:${SERVER_PORT}/login > /dev/null 2>&1; then
        bashio::log.info "OmniRoute is ready."
        READY=true
        break
    fi
    sleep 1
done

if [ "$READY" = false ]; then
    bashio::log.warning "OmniRoute did not become ready within 30 seconds."
fi

# --- Disable dashboard login requirement (bootstrap mode allows loopback) ---
bashio::log.info "Disabling dashboard login requirement for ingress..."
if curl -sf -X PATCH http://localhost:${SERVER_PORT}/api/settings \
    -H "Content-Type: application/json" \
    -d '{"requireLogin": false}' > /dev/null 2>&1; then
    bashio::log.info "Dashboard login requirement disabled successfully."
else
    bashio::log.warning "Could not disable dashboard login. Ingress may require manual configuration."
fi

# --- Forward signals and wait for OmniRoute ---
cleanup() {
    bashio::log.info "Shutting down OmniRoute..."
    kill -TERM "$OMNIRoute_PID" 2>/dev/null
    wait "$OMNIRoute_PID" 2>/dev/null
    exit 0
}
trap cleanup SIGTERM SIGINT

wait "$OMNIRoute_PID"
