#!/usr/bin/with-contenv bashio
# The bashio interpreter enables `set -o errexit errtrace nounset pipefail`. This script turns
# them off deliberately: a single benign non-zero check (e.g. a file that doesn't exist yet on a
# fresh install) would otherwise silently kill the script before its own error handling runs —
# the same bug documented in this repo's fleet-telemetry add-on.
set +e +u +E +o pipefail

DATA_PATH="$(bashio::config 'data_path')"
DOCKER_DATA_ROOT="${DATA_PATH}/docker"
RUNNERS_DIR="${DATA_PATH}/runners"

bashio::log.info "Starting GitHub Runner add-on (data_path=${DATA_PATH})…"

# Fail fast if the USB mount isn't actually there — silently falling back to the container's own
# ephemeral disk would defeat the entire point of the mount (and lose the build cache on restart).
if ! mkdir -p "${DATA_PATH}" 2>/dev/null || [ ! -w "${DATA_PATH}" ]; then
    bashio::log.error "data_path '${DATA_PATH}' is not writable."
    bashio::log.error "Check Settings → System → Storage → Mounts, and that this add-on's" \
        "'media' mapping is enabled."
    exit 1
fi
mkdir -p "${DOCKER_DATA_ROOT}" "${RUNNERS_DIR}"
: > "${DATA_PATH}/dockerd.log"

# The Supervisor mounts /sys/fs/cgroup read-only into every add-on container regardless of
# privileged capabilities — there's no config.yaml key that changes this. Remounting it
# read-write is still possible because it only changes OUR OWN mount namespace's view (not the
# host's), and CAP_SYS_ADMIN is enough to do that even though the underlying mount is read-only.
# Without this, runc fails every nested container create with "mkdir /sys/fs/cgroup/docker:
# read-only file system" — dockerd itself starts fine, but no job can actually run a container.
mount --make-rprivate /sys/fs/cgroup 2>>"${DATA_PATH}/dockerd.log"
mount -o remount,rw /sys/fs/cgroup 2>>"${DATA_PATH}/dockerd.log"

# --- Start the Docker daemon (Docker-in-Docker) ------------------------------------------------
# storage-driver=vfs: overlay2 (the default) requires mounting overlayfs on top of the add-on
# container's own root filesystem, which is itself overlayfs on HAOS — nested overlay-on-overlay
# isn't supported here ("failed to mount overlay: operation not permitted"). vfs has no such
# requirement; it costs more disk per layer, which is exactly what the USB-backed data_path is for.
dockerd --data-root "${DOCKER_DATA_ROOT}" --storage-driver=vfs --host=unix:///var/run/docker.sock \
    >> "${DATA_PATH}/dockerd.log" 2>&1 &
DOCKERD_PID=$!

bashio::log.info "Waiting for the Docker daemon to become ready…"
for _ in $(seq 1 60); do
    docker info >/dev/null 2>&1 && break
    sleep 1
done
if ! docker info >/dev/null 2>&1; then
    bashio::log.error "Docker daemon did not become ready — see ${DATA_PATH}/dockerd.log"
    exit 1
fi
bashio::log.info "Docker daemon ready (data-root=${DOCKER_DATA_ROOT})."

# --- Register + launch one runner process per configured target -------------------------------
declare -A RUNNER_PIDS

start_target() {
    local name="$1" scope="$2" url="$3" token="$4" labels="$5"
    local work_dir="${RUNNERS_DIR}/${name}"

    if ! /opt/scripts/register-target.sh "${name}" "${scope}" "${url}" "${token}" "${labels}" "${work_dir}"; then
        bashio::log.error "[${name}] registration failed — this target will not run. Other targets are unaffected."
        return 1
    fi

    export RUNNER_ALLOW_RUNASROOT="1"
    ( cd "${work_dir}/runner" && exec ./run.sh ) &
    RUNNER_PIDS["${name}"]=$!
    bashio::log.info "[${name}] runner started (pid ${RUNNER_PIDS[${name}]})"
}

target_field() {  # $1 = compact JSON target line, $2 = jq field expression
    echo "$1" | jq -r "$2"
}

mapfile -t TARGET_LINES < <(bashio::config 'targets')
if [ "${#TARGET_LINES[@]}" -eq 0 ]; then
    bashio::log.warning "No targets configured. Add at least one entry under 'targets' in the add-on configuration."
fi

for line in "${TARGET_LINES[@]}"; do
    start_target \
        "$(target_field "${line}" '.name')" \
        "$(target_field "${line}" '.scope')" \
        "$(target_field "${line}" '.url')" \
        "$(target_field "${line}" '.token')" \
        "$(target_field "${line}" '.labels // ""')"
done

# --- Status server (ingress) ---------------------------------------------------------------
# bashio::config emits one bare JSON object per line for a list option (or nothing at all when
# the list is empty) — never a JSON array. jq -s (slurp) wraps that into the array server.py
# expects, and correctly produces "[]" when there are zero targets.
export GH_STATUS_TARGETS="$(bashio::config 'targets' | jq -s -c '.')"
python3 /opt/status_server/server.py &
STATUS_PID=$!
bashio::log.info "Status page listening on :8099"

shutdown() {
    bashio::log.info "Shutting down…"
    for name in "${!RUNNER_PIDS[@]}"; do kill "${RUNNER_PIDS[${name}]}" 2>/dev/null; done
    kill "${STATUS_PID}" 2>/dev/null
    kill "${DOCKERD_PID}" 2>/dev/null
    exit 0
}
trap shutdown TERM INT

# --- Supervision loop: restart any runner process that dies, or the whole add-on if dockerd dies
while true; do
    sleep 10
    for name in "${!RUNNER_PIDS[@]}"; do
        pid="${RUNNER_PIDS[${name}]}"
        if ! kill -0 "${pid}" 2>/dev/null; then
            bashio::log.warning "[${name}] runner process exited; restarting."
            line="$(bashio::config 'targets' | jq -c --arg n "${name}" 'select(.name == $n)')"
            start_target \
                "${name}" \
                "$(target_field "${line}" '.scope')" \
                "$(target_field "${line}" '.url')" \
                "$(target_field "${line}" '.token')" \
                "$(target_field "${line}" '.labels // ""')"
        fi
    done
    if ! kill -0 "${DOCKERD_PID}" 2>/dev/null; then
        bashio::log.error "Docker daemon died — exiting so the Supervisor restarts the add-on."
        exit 1
    fi
done
