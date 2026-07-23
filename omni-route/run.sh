#!/usr/bin/with-contenv bashio
# shellcheck shell=bash

# --- Configuration from options ---
LOG_LEVEL=$(bashio::config 'log_level')
ADMIN_PASSWORD=$(bashio::config 'admin_password')
DASHBOARD_KEY=$(bashio::config 'dashboard_key')
ENCRYPTION_KEY=$(bashio::config 'storage_encryption_key')

bashio::log.info "Starting OmniRoute..."

# --- Build environment ---
# No Home Assistant Ingress here (OmniRoute doesn't support its
# prefix-stripping proxy model — see CHANGELOG). OmniRoute is reached
# directly on its exposed port; put a reverse proxy (e.g. NGINX Proxy
# Manager) in front of it if you want a domain/TLS. Login stays required —
# do NOT disable it here.
export STORAGE_DIR="/data"
export PORT=20128

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

# Admin password: used by OmniRoute to create the admin account on first
# boot. Falls back to a fixed default if not set in the add-on's
# Configuration tab — change it there, or use this default and change it
# from the OmniRoute dashboard after first login.
export INITIAL_PASSWORD="${ADMIN_PASSWORD:-OmniRoute123!}"
bashio::log.info "Dashboard login is required. Default password is set from the 'admin_password' option (or OmniRoute123! if left blank) — change it after first login."

# --- Start OmniRoute in the foreground, forwarding signals ---
omniroute &
OMNIRoute_PID=$!

cleanup() {
    bashio::log.info "Shutting down OmniRoute..."
    kill -TERM "$OMNIRoute_PID" 2>/dev/null
    wait "$OMNIRoute_PID" 2>/dev/null
    exit 0
}
trap cleanup SIGTERM SIGINT

wait "$OMNIRoute_PID"
