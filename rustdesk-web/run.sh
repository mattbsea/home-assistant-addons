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
# apimain binds an internal-only port; nginx (started below) fronts the real ingress-facing port
# 21114 and rewrites apimain's one hardcoded, prefix-unaware redirect (see nginx.conf). Never
# expose 8099 externally -- it's not declared in config.yaml's ports and must stay that way.
export RUSTDESK_API_GIN_API_ADDR="127.0.0.1:8099"
if [ -n "${WS_HOST}" ]; then
    export RUSTDESK_API_RUSTDESK_WS_HOST="${WS_HOST}"
fi

# Persist rustdesk-api's sqlite db into Home Assistant's /data volume. /app/data itself can't
# be replaced with a symlink -- the upstream image declares `VOLUME /app/data`, which makes it a
# live mount point at container start; `rm -rf /app/data` fails with "Resource busy" since only
# a mount point's *contents* are removable from inside the container, not the mount point itself
# (confirmed live: this add-on crashed on exactly that error before this fix). The db path is
# hardcoded upstream as the relative path ./data/rustdeskapi.db (lib/orm/sqlite.go), which
# resolves to /app/data/rustdeskapi.db given this script's `cd /app` below -- so instead of
# symlinking the directory, symlink just that one file, which IS replaceable.
DB_FILE=/app/data/rustdeskapi.db
if [ ! -L "${DB_FILE}" ]; then
    mkdir -p /data
    if [ -f "${DB_FILE}" ] && [ ! -f /data/rustdeskapi.db ]; then
        mv "${DB_FILE}" /data/rustdeskapi.db
    fi
    rm -f "${DB_FILE}"
    ln -s /data/rustdeskapi.db "${DB_FILE}"
fi

cd /app
./apimain &

# nginx is the foreground process (PID 1) so it receives SIGTERM directly on add-on stop/restart;
# apimain is backgrounded behind it. If apimain dies, nginx keeps running and proxies to a dead
# upstream (502s) rather than the container silently looking "up" with nothing behind it -- an
# acceptable tradeoff here since Supervisor's own watchdog/restart handles a fully wedged add-on,
# and this mirrors the plain single-process-supervision level of rigor already used elsewhere in
# this add-on set (e.g. rustdesk-server's hbbs/hbbr, which restart independently but nothing
# restarts run.sh itself if it exits non-zero outside of Supervisor's own container restart).
exec nginx -c /nginx.conf
