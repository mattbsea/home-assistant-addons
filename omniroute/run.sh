#!/usr/bin/with-contenv bashio

set -e
set -o pipefail

# OmniRoute data directory (persistent via /data volume mount)
DATA_DIR="/data/omniroute"

# Initialize the OmniRoute data directory
init_environment() {
    bashio::log.info "Initializing OmniRoute environment..."

    if ! mkdir -p "$DATA_DIR"; then
        bashio::log.error "Failed to create data directory: $DATA_DIR"
        exit 1
    fi

    chown -R omniroute:omniroute "$DATA_DIR"
    bashio::log.info "Data directory: $DATA_DIR"
}

# Run OmniRoute first-time setup if needed
run_setup() {
    local password
    password=$(bashio::config 'omniroute_password' '')

    if [ "$password" = "null" ]; then
        password=""
    fi

    # Only run setup if no database exists yet
    if [ ! -f "$DATA_DIR/omniroute.db" ]; then
        bashio::log.info "Running first-time OmniRoute setup..."

        local setup_args="--non-interactive"
        if [ -n "$password" ]; then
            setup_args="$setup_args --password $password"
            bashio::log.info "Setup with password protection"
        fi

        # shellcheck disable=SC2086
        if gosu omniroute omniroute setup $setup_args 2>&1; then
            bashio::log.info "OmniRoute setup completed"
        else
            bashio::log.warning "OmniRoute setup returned non-zero (may be expected on first run)"
        fi

        chown -R omniroute:omniroute "$DATA_DIR"
    else
        bashio::log.info "Existing OmniRoute installation found, skipping setup"
    fi
}

# Start the OmniRoute gateway
start_omniroute() {
    bashio::log.info "Starting OmniRoute gateway..."

    export HOST="0.0.0.0"
    export PORT="20128"
    export DATA_DIR="$DATA_DIR"

    exec gosu omniroute omniroute
}

# Main execution
main() {
    bashio::log.info "Initializing OmniRoute add-on..."
    init_environment
    run_setup
    start_omniroute
}

main "$@"
