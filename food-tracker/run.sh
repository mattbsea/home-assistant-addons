#!/usr/bin/with-contenv bashio

bashio::log.info "Starting Food Tracker add-on..."

# APP_DIR and DATA_DIR only differ from the defaults when this script is dry-run outside a container.
APP_DIR="${FOOD_TRACKER_APP_DIR:-/app}"
DATA_DIR="${FOOD_TRACKER_DATA_DIR:-/data}"

if ! command -v bun > /dev/null 2>&1; then
    bashio::log.fatal "bun not found in PATH"
    exit 1
fi
if [ ! -f "${APP_DIR}/src/index.ts" ]; then
    bashio::log.fatal "App not found at ${APP_DIR}/src/index.ts"
    exit 1
fi

# Everything the app writes (nutrition.db, keys.json, exports/) is owner-only. The app also sets
# its own modes; this covers the SQLite -wal/-shm files and anything it forgets.
umask 077

# Export a Configuration option as an environment variable. bashio::config.has_value is false for
# unset, empty and the literal "null" Supervisor returns for unset options, so none of those are exported.
export_option() {
    local option="$1" variable="$2"
    if bashio::config.has_value "${option}"; then
        export "${variable}=$(bashio::config "${option}")"
    fi
}

export_option mcp_key MCP_KEY
export_option shortcut_key SHORTCUT_KEY
export_option dashboard_password DASHBOARD_PASSWORD
export_option usda_api_key USDA_API_KEY

# Match src/keys.ts: keys are at least 32 URL-safe characters; the two keys differ.
for key_name in MCP_KEY SHORTCUT_KEY; do
    key_value="${!key_name:-}"
    if [ -n "${key_value}" ] && { [ "${#key_value}" -lt 32 ] || ! [[ "${key_value}" =~ ^[A-Za-z0-9_-]+$ ]]; }; then
        option_name="${key_name,,}"
        bashio::log.fatal "${option_name} must be at least 32 characters using only A-Z, a-z, 0-9, _ and -. Fix it in the Configuration tab, or clear it to have one generated."
        exit 1
    fi
done
if [ -n "${MCP_KEY:-}" ] && [ "${MCP_KEY}" = "${SHORTCUT_KEY:-}" ]; then
    bashio::log.fatal "mcp_key and shortcut_key must be different."
    exit 1
fi
if [ -n "${DASHBOARD_PASSWORD:-}" ]; then
    if [ "${#DASHBOARD_PASSWORD}" -lt 12 ] || [ "${#DASHBOARD_PASSWORD}" -gt 200 ] || [[ "${DASHBOARD_PASSWORD}" == *[$'\001'-$'\037'$'\177']* ]]; then
        bashio::log.fatal "dashboard_password must be 12 to 200 characters with no control characters."
        exit 1
    fi
fi

# Timezone: the option, else Home Assistant's own, else UTC.
TZ_VALUE=""
if bashio::config.has_value 'timezone'; then
    TZ_VALUE="$(bashio::config 'timezone')"
else
    TZ_VALUE="$(bashio::info.timezone 2> /dev/null || true)"
fi
if [ -z "${TZ_VALUE}" ] || [ "${TZ_VALUE}" = "null" ]; then
    TZ_VALUE="UTC"
fi
export TZ="${TZ_VALUE}"
bashio::log.info "Timezone: ${TZ}"

# Fixed by this add-on, not options.
export DATA_DIR
export PORT=8080
export HOST=0.0.0.0
# NGINX Proxy Manager is in front: rate-limit auth failures by the rightmost X-Forwarded-For entry.
export TRUST_PROXY=1
export NODE_ENV=production

mkdir -p "${DATA_DIR}"

# Where the keys are (the app logs this too). The values are never logged.
if bashio::config.has_value 'mcp_key'; then
    bashio::log.info "MCP key: from the mcp_key option"
else
    bashio::log.info "MCP key: generated on first start into ${DATA_DIR}/keys.json (see DOCS.md: Reading your keys)"
fi
if bashio::config.has_value 'shortcut_key'; then
    bashio::log.info "Shortcut key: from the shortcut_key option"
else
    bashio::log.info "Shortcut key: generated on first start into ${DATA_DIR}/keys.json (see DOCS.md: Reading your keys)"
fi
if ! bashio::config.has_value 'dashboard_password'; then
    bashio::log.warning "dashboard_password is not set; set it in the Configuration tab to use the dashboard."
fi

bashio::log.info "Listening on port ${PORT} (host port is set on the Network tab, default 18440)"

# exec: bun replaces this shell, so Supervisor's SIGTERM reaches it directly and the database is
# checkpointed and closed before the add-on stops. --smol keeps memory low.
cd "${APP_DIR}" || exit 1
exec bun --smol src/index.ts
