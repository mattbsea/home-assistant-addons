#!/usr/bin/with-contenv bashio
# Personal AI Infrastructure (PAI) launcher for Home Assistant.
#
# Runs three processes inside the add-on:
#   * Pulse    — the PAI Observatory dashboard      (internal port 31338)
#   * ttyd     — a Claude Code web terminal         (internal port 7683)
#   * gateway  — the ingress entry point, fronting both as tabs (port 31337)
#
# Pulse resolves its own directory as ~/.claude/PAI/PULSE, so the PAI payload
# is installed into $HOME/.claude rather than executed from the git checkout.

set -e
set -o pipefail

PAI_REPO_URL="https://github.com/danielmiessler/Personal_AI_Infrastructure.git"
PAI_CACHE="/data/pai-src"
PULSE_INT_PORT=31338
TTYD_INT_PORT=7683
GATEWAY_PORT=31337

# HOME must be persistent and writable; Pulse and Claude Code use ~/.claude.
export HOME="/data/home"
PAI_CLAUDE="${HOME}/.claude"
PULSE_DIR="${PAI_CLAUDE}/PAI/PULSE"
OBS_DIR="${PULSE_DIR}/Observability"
mkdir -p "${HOME}"

PAI_REF=$(bashio::config 'pai_ref' 'main')
UPDATE_ON_START=$(bashio::config 'update_on_start' 'true')
ENABLE_TERMINAL=$(bashio::config 'enable_terminal' 'true')
# Treat anything other than an explicit "false" as enabled, so run.sh and the
# gateway always agree on the terminal's state.
if [ "${ENABLE_TERMINAL}" = "false" ]; then
    ENABLE_TERMINAL="false"
else
    ENABLE_TERMINAL="true"
fi
NEED_INSTALL=false

# --- Clone or update the PAI repository -------------------------------------
if [ ! -d "${PAI_CACHE}/.git" ]; then
    bashio::log.info "Cloning PAI repository (ref: ${PAI_REF})..."
    rm -rf "${PAI_CACHE}"
    if ! git clone --depth 1 --branch "${PAI_REF}" "${PAI_REPO_URL}" "${PAI_CACHE}"; then
        bashio::log.warning "Ref '${PAI_REF}' not found; cloning the default branch."
        rm -rf "${PAI_CACHE}"
        git clone --depth 1 "${PAI_REPO_URL}" "${PAI_CACHE}"
    fi
    NEED_INSTALL=true
elif [ "${UPDATE_ON_START}" = "true" ]; then
    bashio::log.info "Updating PAI repository..."
    if git -C "${PAI_CACHE}" fetch --depth 1 origin "${PAI_REF}" 2>/dev/null \
        && git -C "${PAI_CACHE}" reset --hard FETCH_HEAD 2>/dev/null; then
        NEED_INSTALL=true
    else
        bashio::log.warning "Update failed; continuing with the existing checkout."
    fi
else
    bashio::log.info "update_on_start disabled; using the existing checkout."
fi

# --- Locate the PAI payload (the .claude tree) inside the checkout ----------
SRC_PULSE=$(find "${PAI_CACHE}" -type d -path '*/.claude/PAI/PULSE' -name PULSE 2>/dev/null \
    | sort | tail -n1)
if [ -z "${SRC_PULSE}" ] || [ ! -f "${SRC_PULSE}/pulse.ts" ]; then
    bashio::log.error "Could not find .claude/PAI/PULSE/pulse.ts in the PAI repository."
    bashio::log.error "The upstream layout may have changed; check the 'pai_ref' option."
    exit 1
fi
SRC_CLAUDE=$(cd "${SRC_PULSE}/../.." && pwd)
bashio::log.info "PAI payload found at: ${SRC_CLAUDE}"

# --- Install / update PAI in ~/.claude --------------------------------------
# The first run seeds the full PAI payload. Later updates refresh the
# framework but never overwrite user-modifiable data, so files created or
# edited through the Claude Code terminal (or by the /interview wizard)
# survive both add-on restarts and updates:
#   * MEMORY, PAI/MEMORY  — durable knowledge and work state
#   * PAI/USER            — the PAI user customization zone
#   * settings.json       — the Digital Assistant identity
#   * .mcp.json, .env     — MCP and environment configuration
# Anything in $HOME outside ~/.claude is never touched by the add-on.
PAI_PRESERVE=(
    --exclude=/MEMORY
    --exclude=/PAI/MEMORY
    --exclude=/PAI/USER
    --exclude=/settings.json
    --exclude=/.mcp.json
    --exclude=/.env
)
if [ ! -f "${PULSE_DIR}/pulse.ts" ]; then
    bashio::log.info "Installing PAI into ${PAI_CLAUDE} (first run)..."
    mkdir -p "${PAI_CLAUDE}"
    cp -a "${SRC_CLAUDE}/." "${PAI_CLAUDE}/"
elif [ "${NEED_INSTALL}" = "true" ]; then
    bashio::log.info "Updating the PAI framework (user data preserved)..."
    rsync -a "${PAI_PRESERVE[@]}" "${SRC_CLAUDE}/" "${PAI_CLAUDE}/"
else
    bashio::log.info "PAI already installed; framework unchanged."
fi

# Pulse refers to its own directory as both "PULSE" and "Pulse"; the mixed-case
# form only resolves on case-insensitive (macOS) filesystems. On Linux a
# symlink is required so the Next.js dashboard assets are found.
if [ ! -e "${PAI_CLAUDE}/PAI/Pulse" ]; then
    ln -s PULSE "${PAI_CLAUDE}/PAI/Pulse"
fi

# --- Generate a clean, add-on-managed Pulse configuration -------------------
# The PAI repo ships the author's personal PULSE.toml — a Telegram bot plus
# per-minute cron jobs that reference machine-specific tools and submodules.
# In a headless add-on those only produce error spam, so a minimal config is
# written instead. It is regenerated on every start.
if bashio::config.has_value 'elevenlabs_api_key'; then
    VOICE_ENABLED="true"
else
    VOICE_ENABLED="false"
fi
cat > "${PULSE_DIR}/PULSE.toml" <<EOF
# Managed by the Home Assistant PAI add-on — regenerated on every start.
[pulse]
port = ${PULSE_INT_PORT}

[telegram]
enabled = false

[imessage]
enabled = false

[voice]
enabled = ${VOICE_ENABLED}

[observability]
enabled = true
dashboard_dir = "Observability/out"

[performance]
enabled = true

[hooks]
enabled = true

[syslog]
enabled = false
EOF
bashio::log.info "Wrote managed PULSE.toml (voice=${VOICE_ENABLED})."

# --- Build ~/.claude/.env from add-on options -------------------------------
ENV_FILE="${PAI_CLAUDE}/.env"
: > "${ENV_FILE}"
if bashio::config.has_value 'elevenlabs_api_key'; then
    echo "ELEVENLABS_API_KEY=$(bashio::config 'elevenlabs_api_key')" >> "${ENV_FILE}"
    bashio::log.info "ElevenLabs API key configured."
fi
if bashio::config.has_value 'extra_env'; then
    while read -r kv; do
        [ -n "${kv}" ] && echo "${kv}" >> "${ENV_FILE}"
    done < <(bashio::config 'extra_env')
fi
chmod 600 "${ENV_FILE}"

# --- Determine the Home Assistant ingress base path -------------------------
# The PAI Observatory is a Next.js app with absolute asset/API paths. To work
# behind ingress (and under the gateway's /pulse tab) it must be rebuilt with
# that path as its base path. The ingress path is read from the Supervisor API.
INGRESS_PATH=""
if [ -n "${SUPERVISOR_TOKEN:-}" ]; then
    INFO=$(curl -s -m 10 -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
        "http://supervisor/addons/self/info" 2>/dev/null || true)
    INGRESS_PATH=$(printf '%s' "${INFO}" | jq -r '.data.ingress_url // empty' 2>/dev/null || true)
    INGRESS_PATH="${INGRESS_PATH%/}"
fi
# The dashboard is served under the gateway's /pulse tab.
BUILD_BP="${INGRESS_PATH}/pulse"
bashio::log.info "Dashboard base path: ${BUILD_BP}"

# --- Build the dashboard for the current base path --------------------------
# The build runs in an isolated directory: installing npm dependencies inside
# the Observability module directory would shadow Bun's on-the-fly resolution
# of Pulse's own runtime imports. Only the finished out/ tree is copied back.
MARKER="${OBS_DIR}/out/.addon-basepath"
BUILD_DIR="/data/dashboard-build"
CURRENT_BP=$(cat "${MARKER}" 2>/dev/null || echo "__unset__")
if [ "${NEED_INSTALL}" = "true" ] || [ "${CURRENT_BP}" != "${BUILD_BP}" ] \
    || [ ! -d "${OBS_DIR}/out" ]; then
    bashio::log.info "Building the PAI Observatory dashboard — the first build can take several minutes..."
    rm -rf "${BUILD_DIR}"
    cp -a "${SRC_CLAUDE}/PAI/PULSE/Observability" "${BUILD_DIR}"
    if /opt/pai/build-dashboard.sh "${BUILD_DIR}" "${BUILD_BP}"; then
        rm -rf "${OBS_DIR}/out"
        cp -a "${BUILD_DIR}/out" "${OBS_DIR}/out"
        printf '%s' "${BUILD_BP}" > "${MARKER}"
        bashio::log.info "Dashboard build complete."
    else
        bashio::log.warning "Dashboard build failed; the prebuilt dashboard will be used (ingress styling may be degraded)."
    fi
    rm -rf "${BUILD_DIR}"
else
    bashio::log.info "Dashboard already built for this base path; skipping rebuild."
fi

# --- Launch Pulse (internal) ------------------------------------------------
export PULSE_PORT="${PULSE_INT_PORT}"
cd "${PULSE_DIR}"
bashio::log.info "Starting PAI Pulse (internal port ${PULSE_INT_PORT})..."
bun run pulse.ts &

# --- Launch the Claude Code terminal (internal) -----------------------------
# ttyd binds to loopback only; the gateway (behind ingress authentication)
# is the sole externally reachable service.
if [ "${ENABLE_TERMINAL}" = "true" ]; then
    bashio::log.info "Starting Claude Code terminal (internal port ${TTYD_INT_PORT})..."
    # Run the terminal inside a persistent tmux session so the Claude Code
    # process survives the browser disconnecting — e.g. switching apps on
    # mobile to complete the sign-in flow. Reconnecting re-attaches to the
    # same session instead of starting a fresh login.
    ttyd --port "${TTYD_INT_PORT}" --interface 127.0.0.1 --base-path /terminal \
        --writable --terminal-type xterm-256color \
        tmux -f /opt/pai/tmux.conf new-session -A -s pai 'cd; claude || true; exec bash' &
else
    bashio::log.info "Claude Code terminal disabled (enable_terminal: false)."
fi

# --- Launch the ingress gateway ---------------------------------------------
bashio::log.info "Starting PAI gateway on ingress port ${GATEWAY_PORT}..."
PAI_GATEWAY_PORT="${GATEWAY_PORT}" \
PAI_PULSE_PORT="${PULSE_INT_PORT}" \
PAI_TTYD_PORT="${TTYD_INT_PORT}" \
PAI_TERMINAL_ENABLED="${ENABLE_TERMINAL}" \
    bun /opt/pai/gateway.ts &

# If any service stops, exit so the Supervisor restarts the add-on.
wait -n || true
bashio::log.warning "A PAI service exited; the add-on will restart."
exit 1
