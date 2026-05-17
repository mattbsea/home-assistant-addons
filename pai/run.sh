#!/usr/bin/with-contenv bashio
# Personal AI Infrastructure (PAI) — Pulse daemon launcher for Home Assistant.
#
# Pulse resolves its own directory as ~/.claude/PAI/PULSE, so the PAI payload
# is installed into $HOME/.claude rather than executed from the git checkout.

set -e
set -o pipefail

PAI_REPO_URL="https://github.com/danielmiessler/Personal_AI_Infrastructure.git"
PAI_CACHE="/data/pai-src"

# HOME must be persistent and writable; Pulse reads ~/.claude/.env from it.
export HOME="/data/home"
PAI_CLAUDE="${HOME}/.claude"
PULSE_DIR="${PAI_CLAUDE}/PAI/PULSE"
mkdir -p "${HOME}"

PAI_REF=$(bashio::config 'pai_ref' 'main')
UPDATE_ON_START=$(bashio::config 'update_on_start' 'true')
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

# --- Install / refresh PAI into ~/.claude -----------------------------------
if [ "${NEED_INSTALL}" = "true" ] || [ ! -f "${PULSE_DIR}/pulse.ts" ]; then
    bashio::log.info "Installing PAI into ${PAI_CLAUDE}..."
    mkdir -p "${PAI_CLAUDE}"
    cp -a "${SRC_CLAUDE}/." "${PAI_CLAUDE}/"
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
port = 31337

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

# --- Launch Pulse -----------------------------------------------------------
# PAI_PULSE_BIND_ALL makes Bun.serve listen on 0.0.0.0 so the HA ingress proxy
# (and the mapped 31337 port) can reach it.
export PAI_PULSE_BIND_ALL=1
export PULSE_PORT=31337

cd "${PULSE_DIR}"
bashio::log.info "Starting PAI Pulse on port ${PULSE_PORT}..."
exec bun run pulse.ts
