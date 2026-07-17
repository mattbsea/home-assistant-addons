#!/usr/bin/with-contenv bashio
set -eo pipefail

DATA_DIR="/data"
KEY_DIR="${DATA_DIR}/rustdesk"
LOG_DIR="${DATA_DIR}/logs"
RUN_DIR="${DATA_DIR}/run"
STATE_FILE="${DATA_DIR}/state.json"
LOG_MAX_BYTES=$((5 * 1024 * 1024))

mkdir -p "${KEY_DIR}" "${LOG_DIR}" "${RUN_DIR}"

# --- Read add-on configuration ------------------------------------------------------------------
RELAY_HOST="$(bashio::config 'relay_host' '')"
[ "${RELAY_HOST}" = "null" ] && RELAY_HOST=""

ENCRYPTED_ONLY="$(bashio::config 'encrypted_only' 'true')"
[ "${ENCRYPTED_ONLY}" = "null" ] && ENCRYPTED_ONLY="true"

CUSTOM_KEY="$(bashio::config 'custom_key' '')"
[ "${CUSTOM_KEY}" = "null" ] && CUSTOM_KEY=""

LOCAL_IP="$(bashio::addon.ip_address 2>/dev/null || echo '')"

if [ -z "${RELAY_HOST}" ]; then
    bashio::log.warning "relay_host is not set — clients outside your LAN will not be able to reach this server. Set relay_host to your public IP/DDNS hostname and forward ports 21115-21119 (21116 also UDP) to this add-on."
fi

# -k flag shared by both hbbs and hbbr: a custom pre-shared key wins over encrypted_only, which
# in turn maps to rustdesk-server's special "_" value (encrypt using the auto-generated keypair).
KEY_ARGS=()
if [ -n "${CUSTOM_KEY}" ]; then
    KEY_ARGS=(-k "${CUSTOM_KEY}")
elif [ "${ENCRYPTED_ONLY}" = "true" ]; then
    KEY_ARGS=(-k _)
fi

# --- Publish state for the dashboard (read-only from its side) ----------------------------------
jq -n \
    --arg relay_host "${RELAY_HOST}" \
    --arg local_ip "${LOCAL_IP}" \
    --argjson encrypted_only "$( [ "${ENCRYPTED_ONLY}" = "true" ] && echo true || echo false )" \
    --argjson custom_key_set "$( [ -n "${CUSTOM_KEY}" ] && echo true || echo false )" \
    '{relay_host: $relay_host, local_ip: $local_ip, encrypted_only: $encrypted_only, custom_key_set: $custom_key_set}' \
    > "${STATE_FILE}"

RD_ADDON_VERSION="$(bashio::addon.version 2>/dev/null || echo '')"
export RD_ADDON_VERSION
export RD_STATE_FILE="${STATE_FILE}"
export RD_RUN_DIR="${RUN_DIR}"
export RD_LOG_DIR="${LOG_DIR}"
export RD_KEY_DIR="${KEY_DIR}"
export RD_WEB_PORT="8092"

# --- Dashboard: always up, never fatal (mirrors the ingress-first pattern used elsewhere in this
# repo) so the status page is reachable even if hbbs/hbbr fail to start. -------------------------
(
    trap 'exit 0' TERM
    while true; do
        ( cd /opt/webapp && exec python3 -m app.main ) &
        child=$!
        wait "${child}"
        bashio::log.warning "dashboard exited; restarting in 3s"
        sleep 3
    done
) &

# --- hbbs / hbbr process management --------------------------------------------------------------
HBBR_PID=""
HBBS_PID=""

start_hbbr() {
    ( cd "${KEY_DIR}" && exec /usr/local/bin/hbbr "${KEY_ARGS[@]}" >>"${LOG_DIR}/hbbr.log" 2>&1 ) &
    HBBR_PID=$!
    echo "${HBBR_PID}" > "${RUN_DIR}/hbbr.pid"
    bashio::log.info "hbbr started (pid ${HBBR_PID})"
}

start_hbbs() {
    local relay_args=()
    if [ -n "${RELAY_HOST}" ]; then
        relay_args=(-r "${RELAY_HOST}:21117")
    fi
    ( cd "${KEY_DIR}" && exec /usr/local/bin/hbbs "${relay_args[@]}" "${KEY_ARGS[@]}" >>"${LOG_DIR}/hbbs.log" 2>&1 ) &
    HBBS_PID=$!
    echo "${HBBS_PID}" > "${RUN_DIR}/hbbs.pid"
    bashio::log.info "hbbs started (pid ${HBBS_PID})"
}

shutdown() {
    bashio::log.info "Shutting down…"
    [ -n "${HBBS_PID}" ] && kill "${HBBS_PID}" 2>/dev/null
    [ -n "${HBBR_PID}" ] && kill "${HBBR_PID}" 2>/dev/null
    exit 0
}
trap shutdown SIGTERM SIGINT

# hbbr first: hbbs' -r flag just advertises the relay address, but starting the relay first avoids
# a startup-order race for clients that connect within the first second.
: > "${LOG_DIR}/hbbr.log"
: > "${LOG_DIR}/hbbs.log"
start_hbbr
sleep 1
start_hbbs

bashio::log.info "RustDesk server ready — dashboard on :${RD_WEB_PORT}, hbbs/hbbr on 21115-21119"

# --- Supervision loop: relaunch either process if it dies; never exit the add-on ------------------
cap_log() {
    local f="$1"
    local size
    size="$(stat -c%s "${f}" 2>/dev/null || echo 0)"
    if [ "${size}" -gt "${LOG_MAX_BYTES}" ]; then
        tail -c "${LOG_MAX_BYTES}" "${f}" > "${f}.tmp" && mv "${f}.tmp" "${f}"
    fi
}

while true; do
    sleep 5

    if ! kill -0 "${HBBR_PID}" 2>/dev/null; then
        bashio::log.warning "hbbr exited unexpectedly; restarting"
        start_hbbr
        sleep 1
        bashio::log.warning "restarting hbbs to re-establish the relay connection"
        [ -n "${HBBS_PID}" ] && kill "${HBBS_PID}" 2>/dev/null
        start_hbbs
    elif ! kill -0 "${HBBS_PID}" 2>/dev/null; then
        bashio::log.warning "hbbs exited unexpectedly; restarting"
        start_hbbs
    fi

    cap_log "${LOG_DIR}/hbbs.log"
    cap_log "${LOG_DIR}/hbbr.log"
done
