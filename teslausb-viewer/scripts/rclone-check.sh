#!/usr/bin/env bash
# Probe that the configured rclone remote/path is reachable.
# Usage: rclone-check.sh <config-path> <remote-name> <remote-path>
# Exits 0 on success, non-zero otherwise. Never blocks startup (caller treats failure as a warning).
set -u

CONF="${1:?config path required}"
REMOTE_NAME="${2:-}"
REMOTE_PATH="${3:-}"

# Default the remote name to the first [section] in the config file.
if [ -z "${REMOTE_NAME}" ] || [ "${REMOTE_NAME}" = "null" ]; then
    REMOTE_NAME="$(grep -m1 -oE '^\[[^]]+\]' "${CONF}" | tr -d '[]')"
fi

if [ -z "${REMOTE_NAME}" ]; then
    echo "rclone-check: no remote name found in ${CONF}" >&2
    exit 1
fi

# Strip leading/trailing slashes from the path for a clean remote:path target.
REMOTE_PATH="${REMOTE_PATH#/}"
REMOTE_PATH="${REMOTE_PATH%/}"
TARGET="${REMOTE_NAME}:${REMOTE_PATH}"

timeout 30 rclone --config "${CONF}" lsd "${TARGET}" >/dev/null 2>&1
