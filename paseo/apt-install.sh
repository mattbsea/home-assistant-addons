#!/usr/bin/env bash
# Installs the `apt_packages` option's Debian packages. Runs as root from run.sh on every start,
# because system packages are lost when the add-on image is rebuilt or updated. Names are checked
# against a strict pattern first, and passed to apt-get as separate arguments (never through a
# shell). Exits non-zero if anything was skipped or failed; the caller logs it and carries on.
# Usage: apt-install.sh [options.json]   (APT_GET overrides the apt-get binary, for tests)
set -u

OPTIONS_FILE="${1:-/data/options.json}"
APT_GET="${APT_GET:-apt-get}"
# Debian policy: lowercase letters, digits, + - . ; at least two characters; starts alphanumeric.
NAME_PATTERN='^[a-z0-9][a-z0-9+.-]+$'

log() {
    echo "[paseo-addon] apt_packages: $*"
}

PACKAGES=()
STATUS=0
while IFS= read -r name; do
    [ -n "${name}" ] || continue
    if [[ "${name}" =~ ${NAME_PATTERN} ]]; then
        PACKAGES+=("${name}")
    else
        log "ERROR: invalid package name '${name}'; skipped."
        STATUS=1
    fi
done < <(jq -r '(.apt_packages // [])[] | tostring' "${OPTIONS_FILE}")

if [ "${#PACKAGES[@]}" -eq 0 ]; then
    exit "${STATUS}"
fi

log "installing ${PACKAGES[*]}"
export DEBIAN_FRONTEND=noninteractive
if "${APT_GET}" update && "${APT_GET}" install -y --no-install-recommends "${PACKAGES[@]}"; then
    log "installed."
else
    log "ERROR: apt-get failed; the packages above may be missing. The daemon will start anyway."
    STATUS=1
fi
exit "${STATUS}"
