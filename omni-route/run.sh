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

# --- Disable dashboard login requirement for ingress ---
bashio::log.info "Disabling dashboard login requirement for ingress..."

# Try bare PATCH first (works in bootstrap mode with no password)
PATCH_RESPONSE=$(curl -s -w "\n%{http_code}" -X PATCH http://localhost:${SERVER_PORT}/api/settings \
    -H "Content-Type: application/json" \
    -d '{"requireLogin": false}')
PATCH_STATUS=$(echo "$PATCH_RESPONSE" | tail -1)

if [ "$PATCH_STATUS" = "200" ]; then
    bashio::log.info "Dashboard login disabled (bootstrap mode)."
else
    # Password is set — authenticate first, then PATCH with currentPassword
    # Use omniroute-reset-password to get the current password from the DB
    CURRENT_PW=$(omniroute-reset-password 2>&1 | sed -n 's/.*New password: //p' | head -1)
    if [ -z "$CURRENT_PW" ]; then
        CURRENT_PW="OmniRoute123!"
    fi

    # Login to get auth token cookie
    LOGIN_RESPONSE=$(curl -s -c /tmp/omniroute_cookies -X POST http://localhost:${SERVER_PORT}/api/auth/login \
        -H "Content-Type: application/json" \
        -d "{\"email\":\"admin@localhost\",\"password\":\"${CURRENT_PW}\"}")

    # PATCH with auth cookie and currentPassword
    PATCH2=$(curl -s -b /tmp/omniroute_cookies -X PATCH http://localhost:${SERVER_PORT}/api/settings \
        -H "Content-Type: application/json" \
        -d "{\"requireLogin\": false, \"currentPassword\": \"${CURRENT_PW}\"}")

    if echo "$PATCH2" | grep -q '"requireLogin":false'; then
        bashio::log.info "Dashboard login disabled (authenticated mode)."
    else
        bashio::log.warning "Could not disable dashboard login. Ingress may require manual configuration."
    fi
    rm -f /tmp/omniroute_cookies
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
