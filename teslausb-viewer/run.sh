#!/usr/bin/with-contenv bashio
# NB: deliberately no `set -e`/`set -o pipefail`. bashio helpers run internal pipelines
# whose benign non-zero stages would abort the whole startup under those options. Each
# step below handles its own errors; the app degrades gracefully if the backend is absent.

APP_DIR="/opt/teslausb-viewer"
DATA_DIR="/data"
RCLONE_CONF="${DATA_DIR}/rclone.conf"
CACHE_DIR="${DATA_DIR}/cache"
PORT=8099

bashio::log.info "Starting TeslaUSB Viewer add-on..."

# --- Initialise persistent storage -----------------------------------------
mkdir -p "${CACHE_DIR}"

# --- Resolve the rclone configuration ---------------------------------------
# Precedence: pasted option -> existing /data/rclone.conf -> guided S3 fields.
if bashio::config.has_value 'rclone_conf'; then
    bashio::log.info "Writing rclone.conf from add-on configuration"
    bashio::config 'rclone_conf' > "${RCLONE_CONF}"
elif [ -f "${RCLONE_CONF}" ]; then
    bashio::log.info "Using existing rclone.conf at ${RCLONE_CONF}"
elif bashio::config.has_value 's3_access_key_id'; then
    bashio::log.info "Synthesising rclone.conf from guided S3 fields"
    REMOTE="$(bashio::config 'remote_name')"
    if [ -z "${REMOTE}" ] || [ "${REMOTE}" = "null" ]; then
        REMOTE="s3"
    fi
    {
        echo "[${REMOTE}]"
        echo "type = s3"
        echo "provider = Other"
        echo "access_key_id = $(bashio::config 's3_access_key_id')"
        echo "secret_access_key = $(bashio::config 's3_secret_access_key')"
        if bashio::config.has_value 's3_endpoint'; then echo "endpoint = $(bashio::config 's3_endpoint')"; fi
        if bashio::config.has_value 's3_region'; then echo "region = $(bashio::config 's3_region')"; fi
    } > "${RCLONE_CONF}"
else
    bashio::log.warning "No backend configured yet — paste your rclone.conf in the add-on"
    bashio::log.warning "Configuration tab, or drop a file at ${RCLONE_CONF}. The UI will still load."
fi

# Lock down credentials and hand /data to the unprivileged user.
if [ -f "${RCLONE_CONF}" ]; then
    chmod 600 "${RCLONE_CONF}"
    bashio::log.info "rclone.conf ready ($(wc -l < "${RCLONE_CONF}") lines)"
fi
chown -R viewer:viewer "${DATA_DIR}" || bashio::log.warning "Could not chown ${DATA_DIR}"

# --- Application configuration via environment ------------------------------
export TUV_DATA_DIR="${DATA_DIR}"
export TUV_RCLONE_CONF="${RCLONE_CONF}"
export TUV_CACHE_DIR="${CACHE_DIR}"
export TUV_REMOTE_NAME="$(bashio::config 'remote_name')"
export TUV_REMOTE_PATH="$(bashio::config 'remote_path')"
export TUV_REFRESH_MINUTES="$(bashio::config 'refresh_interval_minutes')"
export TUV_CACHE_SIZE_MB="$(bashio::config 'cache_size_mb')"
export TUV_PORT="${PORT}"

# Match "today" calculations to Home Assistant's configured timezone.
TZ_VALUE="$(bashio::info.timezone 2>/dev/null)"
if [ -n "${TZ_VALUE}" ] && [ "${TZ_VALUE}" != "null" ]; then
    export TZ="${TZ_VALUE}"
fi

# --- Probe backend reachability (non-fatal) ---------------------------------
if [ -f "${RCLONE_CONF}" ]; then
    if /opt/scripts/rclone-check.sh "${RCLONE_CONF}" "${TUV_REMOTE_NAME}" "${TUV_REMOTE_PATH}"; then
        bashio::log.info "Backend reachable: ${TUV_REMOTE_NAME}:${TUV_REMOTE_PATH}"
    else
        bashio::log.warning "Backend not reachable yet — check credentials/remote_path; the UI will still load"
    fi
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
bashio::log.info "Launching web app on port ${PORT} (ingress)"
cd "${APP_DIR}"
exec gosu viewer "${APP_DIR}/venv/bin/uvicorn" app.main:app \
    --host 0.0.0.0 --port "${PORT}" --workers 1 --no-access-log
