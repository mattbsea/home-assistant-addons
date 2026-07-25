#!/usr/bin/with-contenv bashio

bashio::log.info "Starting OpenCode add-on..."

# Read configuration
LOG_LEVEL=$(bashio::config 'log_level')
bashio::log.info "Log level: ${LOG_LEVEL}"

# Verify opencode is installed
if ! command -v opencode > /dev/null 2>&1; then
    bashio::log.fatal "opencode binary not found in PATH"
    exit 1
fi

OPENCODE_VERSION=$(opencode --version 2>/dev/null || echo "unknown")
bashio::log.info "OpenCode version: ${OPENCODE_VERSION}"

# Set up persistent config directory
# OpenCode stores config at ~/.config/opencode/opencode.json
# Pointing HOME to /data persists config across restarts
export HOME="/data"
mkdir -p /data/.config/opencode

# Set working directory
export WORKDIR="/data"

bashio::log.info "Starting opencode serve on port 4096..."
bashio::log.info "Workspace: ${WORKDIR}"
bashio::log.info "Config: ${HOME}/.config/opencode/"

cd "${WORKDIR}"
exec opencode serve \
    --port 4096 \
    --hostname 0.0.0.0
