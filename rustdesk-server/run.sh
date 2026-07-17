#!/usr/bin/with-contenv bashio
# Runs hbbs (ID/rendezvous server) and hbbr (relay server) concurrently, each restarted
# independently if it exits. bashio enables bash strict mode (-e/-u/-o pipefail) on top of what
# this script sets; that turns any transient benign failure into a silent add-on crash, so we
# explicitly turn it back off (see this repo's fleet-telemetry run.sh for the same pattern).
set +e +u +E +o pipefail

mkdir -p /data/rustdesk
cd /data/rustdesk || exit 1

ENCRYPTED_ONLY="$(bashio::config 'encrypted_only')"
CUSTOM_KEY="$(bashio::config 'custom_key')"
RELAY_HOST="$(bashio::config 'relay_host')"

KEY_ARGS=()
if [ -n "${CUSTOM_KEY}" ]; then
    KEY_ARGS=(-k "${CUSTOM_KEY}")
elif [ "${ENCRYPTED_ONLY}" = "true" ]; then
    KEY_ARGS=(-k _)
fi

RELAY_ARGS=()
if [ -n "${RELAY_HOST}" ]; then
    RELAY_ARGS=(-r "${RELAY_HOST}:21117")
else
    bashio::log.warning "relay_host is not set: clients outside your LAN will not reach this server."
    bashio::log.warning "Set relay_host and forward ports 21115-21119 (21116 also UDP) to reach it remotely."
fi

run_supervised() {
    local name="$1"
    shift
    while true; do
        bashio::log.info "Starting ${name}..."
        "$@"
        bashio::log.warning "${name} exited; restarting in 5s"
        sleep 5
    done
}

run_supervised hbbr /usr/local/bin/hbbr "${KEY_ARGS[@]}" &
run_supervised hbbs /usr/local/bin/hbbs "${RELAY_ARGS[@]}" "${KEY_ARGS[@]}" &

wait
