#!/usr/bin/with-contenv bashio
# The bashio interpreter (/usr/bin/bashio) enables `set -o errexit errtrace nounset pipefail`.
# This script deliberately turns them OFF: it relies on benign non-zero stages (e.g.
# `[ -f "$CFG" ] && …` when the config file doesn't exist yet on a fresh install) and handles its
# own errors. Left on, the first such failure silently kills the script before the supervision
# loop, taking the setup wizard down with it (the bug that broke fresh-install startup).
set +e +u +E +o pipefail
#
# Wizard-driven model: this add-on has NO Configuration-page options. The setup wizard (served by
# the web UI on the ingress port) writes /data/wizard-config.json. Startup is INVERTED — the web
# UI/wizard always starts first and never fatals, so a fresh install with no config is still
# reachable. The cert fetch, the fleet-telemetry binary, the shim and the bridge are deferred and
# (re)started reactively whenever the config file changes.

DATA_DIR="/data"
CERTS_DIR="${DATA_DIR}/certs"
KEYS_DIR="${DATA_DIR}/keys"
CONFIG_JSON="${DATA_DIR}/config.json"
GCP_CREDS="${DATA_DIR}/gcp-credentials.json"
CFG="${DATA_DIR}/wizard-config.json"
BINARY="/usr/local/bin/fleet-telemetry"

# Fixed internal listen ports (host side is remapped in the add-on Network tab).
TELEMETRY_PORT=4443
STATUS_PORT=8080
METRICS_PORT=9090
WEB_PORT=8099
PUBKEY_PORT=8100
RECORDS_FILE="/tmp/ft-records.jsonl"

bashio::log.info "Starting Tesla Fleet Telemetry add-on (wizard-driven)…"
mkdir -p "${CERTS_DIR}" "${KEYS_DIR}"

# --- Config readers (from the wizard config file; never fatal when it is absent) ---------------
# cfg "a.b.c"  -> string value ("" if missing).  cfgb "a.b.c" -> literal true/false.
cfg()  { [ -f "${CFG}" ] && jq -r --arg k "$1" 'getpath($k|split("."))//"" | if type=="object" or type=="array" then "" else tostring end' "${CFG}" 2>/dev/null || echo ""; }
cfgb() { local v; v="$([ -f "${CFG}" ] && jq -r --arg k "$1" 'getpath($k|split("."))//false' "${CFG}" 2>/dev/null)"; [ "${v}" = "true" ] && echo true || echo false; }

config_exists() { [ -f "${CFG}" ]; }
config_ready()  { config_exists && [ -n "$(cfg 'npm.url')" ] && [ -n "$(cfg 'npm.cert_domain')" ]; }

fleet_host_for() {
    case "$1" in
        eu) echo "https://fleet-api.prd.eu.vn.cloud.tesla.com" ;;
        cn) echo "https://fleet-api.prd.cn.vn.cloud.tesla.com" ;;
        *)  echo "https://fleet-api.prd.na.vn.cloud.tesla.com" ;;
    esac
}

# --- Web UI / wizard: ALWAYS first, never fatal ------------------------------------------------
# Reads the config file live per request, so it is never restarted on config change. Export only
# static paths; credentials are read from the config file by the Python process itself.
: > "${RECORDS_FILE}" 2>/dev/null || true
export FT_RECORDS_FILE="${RECORDS_FILE}" FT_WEB_PORT="${WEB_PORT}" FT_PUBKEY_PORT="${PUBKEY_PORT}" \
       FT_CERT_FILE="${CERTS_DIR}/server.crt" \
       FT_SHIM_STATE="${DATA_DIR}/shim-state.json" \
       FT_WIZARD_STATE="${DATA_DIR}/wizard-state.json" \
       FT_WIZARD_CONFIG="${CFG}" \
       FT_PRIVATE_KEY="${KEYS_DIR}/private-key.pem" \
       FT_PUBLIC_KEY="${KEYS_DIR}/public-key.pem" \
       FT_AUTH_HOST="https://auth.tesla.com"
# FT_ADDON_VERSION is provided as a container ENV by the Dockerfile (from BUILD_VERSION).
( trap 'if [ -n "${child:-}" ]; then kill "${child}" 2>/dev/null; fi; exit 0' TERM
  while true; do
    python3 /opt/webapp/server.py & child=$!; wait "${child}"
    bashio::log.warning "web UI exited; restarting in 3s"
    sleep 3
  done ) &
bashio::log.info "Setup wizard + dashboard available via ingress (internal port ${WEB_PORT}); public-key listener on :${PUBKEY_PORT}"

# --- Generate /data/config.json from the wizard config file ------------------------------------
generate_config() {
    # MQTT broker auto-discovery when enabled with a blank broker.
    local mqtt_enabled mqtt_broker mqtt_user mqtt_pass
    mqtt_enabled="$(cfgb 'backends.mqtt.enabled')"
    mqtt_broker="$(cfg 'backends.mqtt.broker')"
    mqtt_user="$(cfg 'backends.mqtt.username')"
    mqtt_pass="$(cfg 'backends.mqtt.password')"
    if [ "${mqtt_enabled}" = "true" ] && [ -z "${mqtt_broker}" ]; then
        if bashio::services.available 'mqtt'; then
            mqtt_broker="$(bashio::services 'mqtt' 'host'):$(bashio::services 'mqtt' 'port')"
            mqtt_user="$(bashio::services 'mqtt' 'username')"
            mqtt_pass="$(bashio::services 'mqtt' 'password')"
            bashio::log.info "Auto-discovered Home Assistant MQTT broker at ${mqtt_broker}"
        else
            bashio::log.warning "MQTT enabled with a blank broker and no HA broker available."
        fi
    fi

    local base
    base="$(jq -n --slurpfile arr "${CFG}" \
        --argjson tport "${TELEMETRY_PORT}" --argjson sport "${STATUS_PORT}" --argjson mport "${METRICS_PORT}" \
        --arg mqtt_broker "${mqtt_broker}" --arg mqtt_user "${mqtt_user}" --arg mqtt_pass "${mqtt_pass}" \
        '
        ($arr[0] // {}) as $c
        | ([ if ($c.backends.logger // true)          then "logger" else empty end,
             if ($c.backends.mqtt.enabled // false)   then "mqtt"   else empty end,
             if ($c.backends.pubsub.enabled // false) then "pubsub" else empty end ]) as $disp0
        | (if ($disp0|length)==0 then ["logger"] else $disp0 end) as $disp
        | {
            host: "0.0.0.0",
            port: $tport,
            status_port: $sport,
            log_level: ($c.server.log_level // "info"),
            json_log_enable: ($c.server.json_log_enable // true),
            namespace: ($c.server.namespace // "tesla_telemetry"),
            reliable_ack: ($c.server.reliable_ack // false),
            rate_limit: {
                enabled: ($c.server.rate_limit_enabled // true),
                message_interval_time: ($c.server.rate_limit_message_interval // 30),
                message_limit: ($c.server.rate_limit_message_limit // 1000)
            },
            records: { V: $disp, connectivity: $disp, alerts: ["logger"], errors: ["logger"] },
            tls: { server_cert: "/data/certs/server.crt", server_key: "/data/certs/server.key" }
          }
        | if ($c.server.metrics_enabled // false)
            then .monitoring = { prometheus_metrics_port: $mport, prometheus_metrics_host: "0.0.0.0" } else . end
        | if ($c.backends.mqtt.enabled // false)
            then .mqtt = (
                { broker: (if $mqtt_broker != "" then $mqtt_broker else ($c.backends.mqtt.broker // "") end),
                  client_id: ($c.backends.mqtt.client_id // "fleet-telemetry"),
                  topic_base: ($c.backends.mqtt.topic_base // "telemetry"),
                  qos: ($c.backends.mqtt.qos // 1) }
                + (if $mqtt_user != "" then { username: $mqtt_user, password: $mqtt_pass }
                   elif (($c.backends.mqtt.username // "")|length) > 0 then { username: $c.backends.mqtt.username, password: ($c.backends.mqtt.password // "") }
                   else {} end)
            ) else . end
        | if ($c.backends.pubsub.enabled // false)
            then .pubsub = { gcp_project_id: ($c.backends.pubsub.gcp_project_id // "") } else . end
        ')"
    [ -z "${base}" ] && { bashio::log.error "Failed to generate config.json from ${CFG}."; return 1; }

    # Deep-merge the optional raw escape hatch over the generated config.
    local extra
    extra="$(cfg 'server.extra_config_json')"
    if [ -n "${extra}" ] && echo "${extra}" | jq empty 2>/dev/null; then
        echo "${base}" | jq --argjson extra "${extra}" '. * $extra' > "${CONFIG_JSON}"
        bashio::log.info "Merged server.extra_config_json over the generated configuration."
    else
        echo "${base}" > "${CONFIG_JSON}"
    fi

    # Google Pub/Sub credentials (written from the config file when present).
    if [ "$(cfgb 'backends.pubsub.enabled')" = "true" ]; then
        local gcp_json
        gcp_json="$([ -f "${CFG}" ] && jq -r '.backends.pubsub.service_account_json // ""' "${CFG}" 2>/dev/null)"
        if [ -n "${gcp_json}" ]; then
            printf '%s' "${gcp_json}" > "${GCP_CREDS}"; chmod 600 "${GCP_CREDS}"
            export GOOGLE_APPLICATION_CREDENTIALS="${GCP_CREDS}"
        elif [ -f "${GCP_CREDS}" ]; then
            export GOOGLE_APPLICATION_CREDENTIALS="${GCP_CREDS}"
        fi
    fi
    return 0
}

fetch_cert() {
    export NPM_URL="$(cfg 'npm.url')" NPM_EMAIL="$(cfg 'npm.email')" \
           NPM_PASSWORD="$([ -f "${CFG}" ] && jq -r '.npm.password // ""' "${CFG}" 2>/dev/null)" \
           NPM_CERT_DOMAIN="$(cfg 'npm.cert_domain')" CERTS_DIR
    /opt/scripts/fetch-npm-cert.sh
}

# --- Process management ------------------------------------------------------------------------
SERVER_PID=""; SHIM_PID=""; BRIDGE_PID=""; TMWS_PID=""

stop_pid() {  # $1 = name of the variable holding the pid
    local var="$1" pid="${!1}"
    if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
        kill "${pid}" 2>/dev/null; wait "${pid}" 2>/dev/null
    fi
    eval "${var}=''"
}

start_server() {
    "${BINARY}" -config="${CONFIG_JSON}" > >(tee -a "${RECORDS_FILE}") 2>&1 &
    SERVER_PID=$!
    bashio::log.info "fleet-telemetry started (pid ${SERVER_PID}) on :${TELEMETRY_PORT} (telemetry), :${STATUS_PORT} (status)"
}

start_shim() {
    export FT_SHIM_PORT="8085" FT_SHIM_STATE="${DATA_DIR}/shim-state.json" \
           FT_SHIM_CLIENT_ID="$(cfg 'tesla.client_id')" \
           FT_SHIM_REFRESH_TOKEN="$([ -f "${CFG}" ] && jq -r '.tesla.shim_refresh_token // ""' "${CFG}" 2>/dev/null)" \
           FT_SHIM_FLEET_HOST="$(fleet_host_for "$(cfg 'tesla.region')")" \
           FT_SHIM_WAKE_ON_PRIME="$([ -f "${CFG}" ] && jq -r '.teslamate.shim_wake_on_prime // true' "${CFG}" 2>/dev/null || echo true)"
    # The wrapper traps TERM and kills its python child — otherwise stop_pid would only kill the
    # subshell and orphan python, which would keep holding :8085 and block the restarted instance.
    ( trap 'if [ -n "${child:-}" ]; then kill "${child}" 2>/dev/null; fi; exit 0' TERM
      while true; do
        python3 /opt/webapp/shim.py & child=$!; wait "${child}"
        bashio::log.warning "Fleet-API shim exited; restarting in 3s"; sleep 3
      done ) &
    SHIM_PID=$!
    bashio::log.info "Fleet-API shim listening on :8085"
}

start_bridge() {
    local target="" ext
    ext="$(cfg 'teslamate.bridge_url')"
    if [ -n "${ext}" ]; then
        target="${ext}"
        bashio::log.info "TeslaMate bridge -> external websocket server ${target}"
    elif [ "$(cfgb 'teslamate.bridge_enabled')" = "true" ]; then
        ( trap 'if [ -n "${child:-}" ]; then kill "${child}" 2>/dev/null; fi; exit 0' TERM
          while true; do
            node /opt/teslamate-ws/index.js & child=$!; wait "${child}"
            bashio::log.warning "TeslaMate websocket server exited; restarting in 5s"; sleep 5
          done ) &
        TMWS_PID=$!
        target="http://127.0.0.1:8081/"
        bashio::log.info "Bundled TeslaMate websocket server started on :8081"
    fi
    if [ -n "${target}" ]; then
        export FT_RECORDS_FILE="${RECORDS_FILE}" FT_BRIDGE_URL="${target}"
        ( trap 'if [ -n "${child:-}" ]; then kill "${child}" 2>/dev/null; fi; exit 0' TERM
          while true; do
            python3 /opt/webapp/bridge.py & child=$!; wait "${child}"
            bashio::log.warning "TeslaMate bridge exited; restarting in 5s"; sleep 5
          done ) &
        BRIDGE_PID=$!
        bashio::log.info "TeslaMate bridge forwarding telemetry to ${target}"
    fi
}

# Re-(generate config, fetch cert, launch services) to match the current config file.
reconcile() {
    config_exists || return 0
    bashio::log.info "Applying configuration from ${CFG}…"

    # Telemetry binary: requires a cert (mTLS-only). Fetch from NPM when configured.
    stop_pid SERVER_PID
    if config_ready; then
        if ! fetch_cert; then
            if [ -f "${CERTS_DIR}/server.crt" ]; then
                bashio::log.warning "NPM cert fetch failed — using the cached certificate."
            else
                bashio::log.warning "No certificate yet (NPM fetch failed, none cached) — telemetry server not started. Finish the NPM steps in the wizard."
            fi
        fi
    fi
    if generate_config && [ -f "${CERTS_DIR}/server.crt" ] && [ -f "${CERTS_DIR}/server.key" ]; then
        jq 'del(.mqtt.password)' "${CONFIG_JSON}" 2>/dev/null | while IFS= read -r line; do bashio::log.info "  ${line}"; done
        start_server
    else
        bashio::log.info "Telemetry server deferred until a certificate is available."
    fi

    # Shim + bridge do not need the cert; (re)start them to pick up credential/integration changes.
    stop_pid SHIM_PID
    start_shim
    stop_pid BRIDGE_PID
    stop_pid TMWS_PID
    start_bridge
}

shutdown() {
    bashio::log.info "Shutting down…"
    stop_pid SERVER_PID; stop_pid SHIM_PID; stop_pid BRIDGE_PID; stop_pid TMWS_PID
    exit 0
}
trap shutdown SIGTERM SIGINT

# --- Reactive supervision loop -----------------------------------------------------------------
# The config file IS the restart signal: hash it each tick and reconcile on change. The add-on is
# NEVER exited when a child dies — that would take the wizard down with it; children are relaunched.
LAST_HASH=""
CERT_REFRESH_HOURS="$(cfg 'npm.cert_refresh_hours')"; [ -z "${CERT_REFRESH_HOURS}" ] && CERT_REFRESH_HOURS=12
REFRESH_SECS=$(( CERT_REFRESH_HOURS * 3600 ))
POLL_SECS=5
elapsed=0

while true; do
    NEW_HASH="$( [ -f "${CFG}" ] && sha256sum "${CFG}" 2>/dev/null | awk '{print $1}' )"
    if [ "${NEW_HASH}" != "${LAST_HASH}" ]; then
        LAST_HASH="${NEW_HASH}"
        CERT_REFRESH_HOURS="$(cfg 'npm.cert_refresh_hours')"; [ -z "${CERT_REFRESH_HOURS}" ] && CERT_REFRESH_HOURS=12
        REFRESH_SECS=$(( CERT_REFRESH_HOURS * 3600 )); elapsed=0
        reconcile
    fi

    sleep "${POLL_SECS}"

    # Relaunch the telemetry binary if it died while we expect it to be running (do NOT exit).
    if [ -n "${SERVER_PID}" ] && ! kill -0 "${SERVER_PID}" 2>/dev/null; then
        wait "${SERVER_PID}" 2>/dev/null; rc=$?
        bashio::log.warning "fleet-telemetry exited (rc=${rc}); relaunching in 3s."
        SERVER_PID=""
        sleep 3
        if config_ready && [ -f "${CONFIG_JSON}" ] && [ -f "${CERTS_DIR}/server.crt" ]; then
            start_server
        fi
    fi

    # Periodic certificate refresh — restart only the binary if the cert changed.
    if config_ready; then
        elapsed=$(( elapsed + POLL_SECS ))
        if [ "${elapsed}" -ge "${REFRESH_SECS}" ]; then
            elapsed=0
            OLD_CHASH="$(sha256sum "${CERTS_DIR}/server.crt" 2>/dev/null | awk '{print $1}')"
            if fetch_cert; then
                NEW_CHASH="$(sha256sum "${CERTS_DIR}/server.crt" 2>/dev/null | awk '{print $1}')"
                if [ "${OLD_CHASH}" != "${NEW_CHASH}" ]; then
                    bashio::log.info "Certificate changed on renewal — restarting fleet-telemetry."
                    stop_pid SERVER_PID
                    [ -f "${CONFIG_JSON}" ] && start_server
                fi
            fi
        fi
    fi
done
