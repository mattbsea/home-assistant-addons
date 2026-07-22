#!/usr/bin/with-contenv bashio

set -e
set -o pipefail

# OmniRoute data directory (persistent via /data volume mount)
DATA_DIR="/data/omniroute"

# Initialize the OmniRoute data directory
init_environment() {
    bashio::log.info "=== OmniRoute Add-on Starting ==="
    bashio::log.info "Initializing OmniRoute environment..."

    if ! mkdir -p "$DATA_DIR"; then
        bashio::log.error "Failed to create data directory: $DATA_DIR"
        exit 1
    fi

    chown -R omniroute:omniroute "$DATA_DIR"
    bashio::log.info "Data directory: $DATA_DIR"
    bashio::log.info "UID/GID of omniroute user: $(id omniroute 2>&1 || echo 'user not found')"
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

    bashio::log.info "Database exists: $([ -f "$DATA_DIR/omniroute.db" ] && echo 'yes' || echo 'no')"
    ls -la "$DATA_DIR/" 2>&1 | head -20
}

# Validate and start nginx reverse proxy (listens on ingress port 20128)
start_nginx() {
    bashio::log.info "=== Starting nginx ingress proxy ==="

    # Log the nginx config we're actually using
    bashio::log.info "nginx config at /etc/nginx/sites-enabled/ingress.conf:"
    cat /etc/nginx/sites-enabled/ingress.conf 2>&1

    # Validate nginx config
    if ! nginx -t 2>&1; then
        bashio::log.error "nginx config test FAILED — aborting"
        exit 1
    fi
    bashio::log.info "nginx config test PASSED"

    # Start nginx in background
    nginx
    bashio::log.info "nginx started on port 20128"

    # Verify nginx is actually listening
    sleep 1
    if curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:20128/ 2>/dev/null | grep -qE '^[2345]'; then
        bashio::log.info "nginx port 20128 is responding (HTTP $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:20128/))"
    else
        bashio::log.warning "nginx port 20128 did not respond to test request"
    fi
}

# Main execution
main() {
    bashio::log.info "=== OmniRoute Add-on v$(cat /data/omniroute/version 2>/dev/null || echo 'dev') ==="
    bashio::log.info "Initializing OmniRoute add-on..."
    bashio::log.info "Environment: HOST=0.0.0.0 PORT=20129 DATA_DIR=$DATA_DIR"
    init_environment
    run_setup
    start_nginx
    bashio::log.info "=== Starting OmniRoute gateway on port 20129 ==="

    export HOST="0.0.0.0"
    export PORT="20129"
    export DATA_DIR="$DATA_DIR"

    exec gosu omniroute omniroute 2>&1
}

main "$@"
