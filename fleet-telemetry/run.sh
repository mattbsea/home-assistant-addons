#!/usr/bin/with-contenv bashio
# NB: deliberately no `set -e`/`set -o pipefail`. bashio helpers run internal pipelines whose
# benign non-zero stages would abort startup under those options. Each step handles its own errors.

DATA_DIR="/data"
CERTS_DIR="${DATA_DIR}/certs"
CONFIG_JSON="${DATA_DIR}/config.json"
OPTIONS_JSON="${DATA_DIR}/options.json"
GCP_CREDS="${DATA_DIR}/gcp-credentials.json"
BINARY="/usr/local/bin/fleet-telemetry"

# Fixed internal listen ports (host side is remapped in the add-on Network tab)
TELEMETRY_PORT=4443
STATUS_PORT=8080
METRICS_PORT=9090
WEB_PORT=8099
RECORDS_FILE="/tmp/ft-records.jsonl"

bashio::log.info "Starting Tesla Fleet Telemetry add-on..."
mkdir -p "${CERTS_DIR}"

# --- Helpers ----------------------------------------------------------------
# Emit a literal true/false for jq --argjson from a bashio bool option.
jbool() { if bashio::config.true "$1"; then echo true; else echo false; fi; }
# Read a (possibly multiline) string option straight from options.json — bashio mangles multiline.
opt_raw() { jq -r --arg k "$1" '.[$k] // ""' "${OPTIONS_JSON}" 2>/dev/null; }

# --- Read scalar options ----------------------------------------------------
LOG_LEVEL="$(bashio::config 'log_level')"
NAMESPACE="$(bashio::config 'namespace')"
JSON_LOG="$(jbool 'json_log_enable')"
RELIABLE_ACK="$(jbool 'reliable_ack')"
RL_ENABLED="$(jbool 'rate_limit_enabled')"
RL_INTERVAL="$(bashio::config 'rate_limit_message_interval')"
RL_LIMIT="$(bashio::config 'rate_limit_message_limit')"
METRICS_ENABLED="$(jbool 'metrics_enabled')"

ENABLE_LOGGER="$(jbool 'enable_logger')"
ENABLE_MQTT="$(jbool 'enable_mqtt')"
ENABLE_PUBSUB="$(jbool 'enable_pubsub')"

# Guarantee at least one data dispatcher so records.V is never empty (invalid config).
if [ "${ENABLE_LOGGER}" != "true" ] && [ "${ENABLE_MQTT}" != "true" ] && [ "${ENABLE_PUBSUB}" != "true" ]; then
    bashio::log.warning "No backend enabled — defaulting to the logger backend."
    ENABLE_LOGGER="true"
fi

# --- TLS certificate from Nginx Proxy Manager -------------------------------
export NPM_URL="$(bashio::config 'npm_url')"
export NPM_EMAIL="$(bashio::config 'npm_email')"
export NPM_PASSWORD="$(bashio::config 'npm_password')"
export NPM_CERT_DOMAIN="$(bashio::config 'npm_cert_domain')"
export CERTS_DIR
CERT_REFRESH_HOURS="$(bashio::config 'cert_refresh_hours')"

fetch_cert() { /opt/scripts/fetch-npm-cert.sh; }

if bashio::var.is_empty "${NPM_URL}" || bashio::var.is_empty "${NPM_CERT_DOMAIN}"; then
    bashio::log.fatal "npm_url and npm_cert_domain are required — the server is mTLS-only and needs a TLS cert."
    exit 1
fi

bashio::log.info "Fetching TLS certificate for ${NPM_CERT_DOMAIN} from NPM..."
if ! fetch_cert; then
    if [ -f "${CERTS_DIR}/server.crt" ] && [ -f "${CERTS_DIR}/server.key" ]; then
        bashio::log.warning "NPM fetch failed — using the previously cached certificate."
    else
        bashio::log.fatal "Could not fetch a certificate from NPM and none is cached. Check npm_* settings."
        exit 1
    fi
fi

# --- Google Pub/Sub credentials ---------------------------------------------
if [ "${ENABLE_PUBSUB}" = "true" ]; then
    GCP_JSON="$(opt_raw 'gcp_service_account_json')"
    if [ -n "${GCP_JSON}" ]; then
        printf '%s' "${GCP_JSON}" > "${GCP_CREDS}"
        chmod 600 "${GCP_CREDS}"
        export GOOGLE_APPLICATION_CREDENTIALS="${GCP_CREDS}"
        bashio::log.info "Wrote Google service-account credentials to ${GCP_CREDS}"
    elif [ -f "${GCP_CREDS}" ]; then
        export GOOGLE_APPLICATION_CREDENTIALS="${GCP_CREDS}"
        bashio::log.info "Using cached Google service-account credentials."
    else
        bashio::log.warning "Pub/Sub enabled but no gcp_service_account_json provided — it will likely fail to authenticate."
    fi
fi

# --- MQTT broker (auto-discover the HA broker when broker left blank) --------
MQTT_BROKER="$(bashio::config 'mqtt_broker')"
MQTT_USERNAME="$(bashio::config 'mqtt_username')"
MQTT_PASSWORD="$(bashio::config 'mqtt_password')"
if [ "${ENABLE_MQTT}" = "true" ] && bashio::var.is_empty "${MQTT_BROKER}"; then
    if bashio::services.available 'mqtt'; then
        MQTT_BROKER="$(bashio::services 'mqtt' 'host'):$(bashio::services 'mqtt' 'port')"
        MQTT_USERNAME="$(bashio::services 'mqtt' 'username')"
        MQTT_PASSWORD="$(bashio::services 'mqtt' 'password')"
        bashio::log.info "Auto-discovered Home Assistant MQTT broker at ${MQTT_BROKER}"
    else
        bashio::log.warning "MQTT enabled with a blank broker and no HA broker available — set mqtt_broker."
    fi
fi

# --- Generate /data/config.json ---------------------------------------------
generate_config() {
    jq -n \
        --arg log_level "${LOG_LEVEL}" \
        --argjson json_log "${JSON_LOG}" \
        --arg namespace "${NAMESPACE}" \
        --argjson reliable_ack "${RELIABLE_ACK}" \
        --argjson tport "${TELEMETRY_PORT}" \
        --argjson sport "${STATUS_PORT}" \
        --argjson rl_enabled "${RL_ENABLED}" \
        --argjson rl_interval "${RL_INTERVAL}" \
        --argjson rl_limit "${RL_LIMIT}" \
        --argjson metrics "${METRICS_ENABLED}" \
        --argjson mport "${METRICS_PORT}" \
        --argjson logger "${ENABLE_LOGGER}" \
        --argjson mqtt "${ENABLE_MQTT}" \
        --argjson pubsub "${ENABLE_PUBSUB}" \
        --arg mqtt_broker "${MQTT_BROKER}" \
        --arg mqtt_client_id "$(bashio::config 'mqtt_client_id')" \
        --arg mqtt_topic_base "$(bashio::config 'mqtt_topic_base')" \
        --argjson mqtt_qos "$(bashio::config 'mqtt_qos')" \
        --arg mqtt_username "${MQTT_USERNAME}" \
        --arg mqtt_password "${MQTT_PASSWORD}" \
        --arg gcp_project_id "$(bashio::config 'gcp_project_id')" \
        '
        ([ if $logger then "logger" else empty end,
           if $mqtt   then "mqtt"   else empty end,
           if $pubsub then "pubsub" else empty end ]) as $disp
        | {
            host: "0.0.0.0",
            port: $tport,
            status_port: $sport,
            log_level: $log_level,
            json_log_enable: $json_log,
            namespace: $namespace,
            reliable_ack: $reliable_ack,
            rate_limit: { enabled: $rl_enabled, message_interval_time: $rl_interval, message_limit: $rl_limit },
            records: { V: $disp, connectivity: $disp, alerts: ["logger"], errors: ["logger"] },
            tls: { server_cert: "/data/certs/server.crt", server_key: "/data/certs/server.key" }
          }
        | if $metrics then .monitoring = { prometheus_metrics_port: $mport, prometheus_metrics_host: "0.0.0.0" } else . end
        | if $mqtt then .mqtt = (
              { broker: $mqtt_broker, client_id: $mqtt_client_id, topic_base: $mqtt_topic_base, qos: $mqtt_qos }
              + (if $mqtt_username != "" then { username: $mqtt_username, password: $mqtt_password } else {} end)
          ) else . end
        | if $pubsub then .pubsub = { gcp_project_id: $gcp_project_id } else . end
        '
}

BASE_CONFIG="$(generate_config)"
if [ -z "${BASE_CONFIG}" ]; then
    bashio::log.fatal "Failed to generate config.json from options."
    exit 1
fi

# Deep-merge the optional raw escape hatch over the generated config.
EXTRA_JSON="$(opt_raw 'extra_config_json')"
if [ -n "${EXTRA_JSON}" ]; then
    if echo "${EXTRA_JSON}" | jq empty 2>/dev/null; then
        echo "${BASE_CONFIG}" | jq --argjson extra "${EXTRA_JSON}" '. * $extra' > "${CONFIG_JSON}"
        bashio::log.info "Merged extra_config_json over the generated configuration."
    else
        bashio::log.error "extra_config_json is not valid JSON — ignoring it."
        echo "${BASE_CONFIG}" > "${CONFIG_JSON}"
    fi
else
    echo "${BASE_CONFIG}" > "${CONFIG_JSON}"
fi

bashio::log.info "Effective configuration (secrets omitted):"
jq 'del(.mqtt.password)' "${CONFIG_JSON}" | while IFS= read -r line; do bashio::log.info "  ${line}"; done

# --- Telemetry dashboard (ingress, read-only) -------------------------------
# Independent of the telemetry path: it only tails a copy of the logger output, so if it
# dies the vehicle stream is unaffected. Restarted in a loop to keep the panel available.
: > "${RECORDS_FILE}" 2>/dev/null || true
export FT_RECORDS_FILE="${RECORDS_FILE}" FT_WEB_PORT="${WEB_PORT}" \
       FT_CERT_FILE="${CERTS_DIR}/server.crt" FT_NAMESPACE="${NAMESPACE}"
( while true; do
    python3 /opt/webapp/server.py
    bashio::log.warning "dashboard exited; restarting in 3s"
    sleep 3
  done ) &
bashio::log.info "Telemetry dashboard available via ingress (internal port ${WEB_PORT})"

# --- TeslaMate bridge (optional) --------------------------------------------
# Forwards decoded records to a MyTeslaMate websocket server (POST /), replacing the Google
# Pub/Sub push so TeslaMate can stream fully self-hosted. The websocket server can be bundled
# (runs here on :8081) or external (teslamate_bridge_url). Isolated from the telemetry path.
TM_TARGET=""
EXT_URL="$(bashio::config 'teslamate_bridge_url')"
if [ -n "${EXT_URL}" ] && [ "${EXT_URL}" != "null" ]; then
    TM_TARGET="${EXT_URL}"
    bashio::log.info "TeslaMate bridge -> external websocket server ${TM_TARGET}"
elif bashio::config.true 'enable_teslamate_bridge'; then
    ( while true; do
        node /opt/teslamate-ws/index.js
        bashio::log.warning "TeslaMate websocket server exited; restarting in 5s"
        sleep 5
      done ) &
    TM_TARGET="http://127.0.0.1:8081/"
    bashio::log.info "Bundled TeslaMate websocket server started on :8081 (front with TLS for wss://)"
fi
if [ -n "${TM_TARGET}" ]; then
    export FT_RECORDS_FILE="${RECORDS_FILE}" FT_BRIDGE_URL="${TM_TARGET}"
    ( while true; do
        python3 /opt/webapp/bridge.py
        bashio::log.warning "TeslaMate bridge exited; restarting in 5s"
        sleep 5
      done ) &
    bashio::log.info "TeslaMate bridge forwarding telemetry to ${TM_TARGET}"
fi

# --- Run the server with cert-refresh supervision ---------------------------
SERVER_PID=""
start_server() {
    # Tee the binary's stdout/stderr to the records file (for the dashboard) while still
    # surfacing it in the add-on log. $! remains the binary's PID for clean kill/restart.
    "${BINARY}" -config="${CONFIG_JSON}" > >(tee -a "${RECORDS_FILE}") 2>&1 &
    SERVER_PID=$!
    bashio::log.info "fleet-telemetry started (pid ${SERVER_PID}) listening on :${TELEMETRY_PORT} (telemetry), :${STATUS_PORT} (status)"
}

shutdown() {
    bashio::log.info "Shutting down..."
    [ -n "${SERVER_PID}" ] && kill "${SERVER_PID}" 2>/dev/null
    wait "${SERVER_PID}" 2>/dev/null
    exit 0
}
trap shutdown SIGTERM SIGINT

start_server

REFRESH_SECS=$(( CERT_REFRESH_HOURS * 3600 ))
POLL_SECS=5
elapsed=0
while true; do
    sleep "${POLL_SECS}"
    if ! kill -0 "${SERVER_PID}" 2>/dev/null; then
        wait "${SERVER_PID}"; rc=$?
        bashio::log.warning "fleet-telemetry exited (rc=${rc}). Exiting so Supervisor restarts the add-on."
        exit "${rc}"
    fi
    elapsed=$(( elapsed + POLL_SECS ))
    if [ "${elapsed}" -ge "${REFRESH_SECS}" ]; then
        elapsed=0
        OLD_HASH="$(sha256sum "${CERTS_DIR}/server.crt" 2>/dev/null | awk '{print $1}')"
        if fetch_cert; then
            NEW_HASH="$(sha256sum "${CERTS_DIR}/server.crt" 2>/dev/null | awk '{print $1}')"
            if [ "${OLD_HASH}" != "${NEW_HASH}" ]; then
                bashio::log.info "Certificate changed on renewal — restarting fleet-telemetry to load it."
                kill "${SERVER_PID}" 2>/dev/null
                wait "${SERVER_PID}" 2>/dev/null
                start_server
            fi
        fi
    fi
done
