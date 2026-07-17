#!/usr/bin/with-contenv bashio
# NB: deliberately no `set -e`/`set -o pipefail`. bashio helpers run internal pipelines
# whose benign non-zero stages would abort the whole startup under those options. Each
# step below handles its own errors; the app degrades gracefully if teslacam_path is absent.

APP_DIR="/opt/teslausb-viewer"
DATA_DIR="/data"
CACHE_DIR="${DATA_DIR}/cache"
PORT=8099

bashio::log.info "Starting TeslaUSB Viewer add-on..."

# --- Initialise persistent storage -----------------------------------------
mkdir -p "${CACHE_DIR}"

# --- Resolve and prepare the local TeslaCam directory -----------------------
TESLACAM_PATH="$(bashio::config 'teslacam_path')"
if [ -z "${TESLACAM_PATH}" ] || [ "${TESLACAM_PATH}" = "null" ]; then
    TESLACAM_PATH="/media/USBDisk/teslausb"
fi
mkdir -p "${TESLACAM_PATH}" || bashio::log.warning "Could not create ${TESLACAM_PATH}"

# This add-on is the sole owner/writer of teslacam_path (the Pi archiver writes over the
# network via the upload API, not directly to the filesystem), so a recursive chown at
# startup is safe — same pattern as /data below.
chown -R viewer:viewer "${DATA_DIR}" || bashio::log.warning "Could not chown ${DATA_DIR}"
chown -R viewer:viewer "${TESLACAM_PATH}" || bashio::log.warning "Could not chown ${TESLACAM_PATH}"

# --- Application configuration via environment ------------------------------
export TUV_DATA_DIR="${DATA_DIR}"
export TUV_TESLACAM_PATH="${TESLACAM_PATH}"
export TUV_CACHE_DIR="${CACHE_DIR}"
export TUV_REFRESH_MINUTES="$(bashio::config 'refresh_interval_minutes')"
export TUV_CACHE_SIZE_MB="$(bashio::config 'cache_size_mb')"
export TUV_PORT="${PORT}"

# Match "today" calculations to Home Assistant's configured timezone.
TZ_VALUE="$(bashio::info.timezone 2>/dev/null)"
if [ -n "${TZ_VALUE}" ] && [ "${TZ_VALUE}" != "null" ]; then
    export TZ="${TZ_VALUE}"
fi

if [ -d "${TESLACAM_PATH}" ]; then
    bashio::log.info "TeslaCam directory ready: ${TESLACAM_PATH}"
else
    bashio::log.warning "teslacam_path (${TESLACAM_PATH}) is not a directory — the UI will still load"
fi

# --- MQTT (optional) --------------------------------------------------------
if bashio::config.true 'publish_mqtt' && bashio::services.available 'mqtt'; then
    export TUV_MQTT_ENABLED="true"
    export TUV_MQTT_HOST="$(bashio::services 'mqtt' 'host')"
    export TUV_MQTT_PORT="$(bashio::services 'mqtt' 'port')"
    export TUV_MQTT_USERNAME="$(bashio::services 'mqtt' 'username')"
    export TUV_MQTT_PASSWORD="$(bashio::services 'mqtt' 'password')"
    bashio::log.info "MQTT broker discovered — statistics entities will be published"
else
    export TUV_MQTT_ENABLED="false"
    bashio::log.info "MQTT disabled or no broker available — skipping statistics entities"
fi

# --- Launch the web app as the unprivileged user ----------------------------
bashio::log.info "Launching web app on port ${PORT} (ingress + LAN upload API)"
cd "${APP_DIR}"
exec gosu viewer "${APP_DIR}/venv/bin/uvicorn" app.main:app \
    --host 0.0.0.0 --port "${PORT}" --workers 1 --no-access-log
