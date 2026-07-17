#!/bin/sh
# No bashio here (see Dockerfile) -- read options.json with jq directly. set -eu is safe in
# this plain sh script (unlike the bashio-based rustdesk-server run.sh, nothing upstream of
# this script has already enabled strict mode in a way that turns benign failures fatal).
set -eu

OPTIONS_FILE="/data/options.json"
WS_HOST=""
if [ -f "${OPTIONS_FILE}" ]; then
    WS_HOST="$(jq -r '.ws_host // ""' "${OPTIONS_FILE}")"
fi

export RUSTDESK_API_LANG="en"
export RUSTDESK_API_RUSTDESK_ID_SERVER="a44b0313-rustdesk-server:21116"
export RUSTDESK_API_RUSTDESK_RELAY_SERVER="a44b0313-rustdesk-server:21117"
export RUSTDESK_API_RUSTDESK_API_SERVER="http://a44b0313-rustdesk-web:21114"
if [ -n "${WS_HOST}" ]; then
    export RUSTDESK_API_RUSTDESK_WS_HOST="${WS_HOST}"
fi

# Persist rustdesk-api's sqlite db (users, address book, logs) into Home Assistant's /data
# volume instead of the image's own /app/data, the same symlink-into-/data pattern this repo
# uses elsewhere (e.g. claude-terminal's credential directory).
if [ ! -L /app/data ]; then
    mkdir -p /data
    if [ -d /app/data ] && [ -z "$(ls -A /data 2>/dev/null)" ]; then
        cp -a /app/data/. /data/ 2>/dev/null || true
    fi
    rm -rf /app/data
    ln -s /data /app/data
fi

cd /app
exec ./apimain
