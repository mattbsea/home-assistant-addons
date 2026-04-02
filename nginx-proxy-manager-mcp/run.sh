#!/usr/bin/with-contenv bashio

bashio::log.info "Starting Nginx Proxy Manager MCP add-on..."

# Read config
bashio::log.info "Reading configuration..."
NPM_URL=$(bashio::config 'npm_url')
NPM_EMAIL=$(bashio::config 'npm_email')
NPM_PASSWORD=$(bashio::config 'npm_password')

# Validate required fields
if bashio::var.is_empty "${NPM_URL}"; then
    bashio::log.fatal "npm_url is not set — configure it in the add-on Configuration tab"
    exit 1
fi
if bashio::var.is_empty "${NPM_EMAIL}"; then
    bashio::log.fatal "npm_email is not set — configure it in the add-on Configuration tab"
    exit 1
fi
if bashio::var.is_empty "${NPM_PASSWORD}"; then
    bashio::log.fatal "npm_password is not set — configure it in the add-on Configuration tab"
    exit 1
fi
bashio::log.info "Configuration looks good"

# Verify MCP server exists
if [ ! -f "/opt/npm-mcp/server.py" ]; then
    bashio::log.fatal "MCP server not found at /opt/npm-mcp/server.py"
    exit 1
fi
bashio::log.info "MCP server found"

# Determine secret path: use config option if set, otherwise generate/load from /data
SECRET_FILE="/data/secret_path.txt"
SECRET_PATH=""

if bashio::config.has_value 'secret_path'; then
    SECRET_PATH=$(bashio::config 'secret_path')
    bashio::log.info "Using secret path from add-on configuration"
fi

if bashio::var.is_empty "${SECRET_PATH}"; then
    # Auto-generate and persist
    if [ ! -f "${SECRET_FILE}" ]; then
        bashio::log.info "Generating new secret path..."
        SECRET_PATH="private_$(openssl rand -hex 16)"
        echo "${SECRET_PATH}" > "${SECRET_FILE}"
        bashio::log.info "Secret path generated and saved to ${SECRET_FILE}"
    else
        SECRET_PATH=$(cat "${SECRET_FILE}")
        bashio::log.info "Loading existing secret path from ${SECRET_FILE}"
    fi
    bashio::log.info "Tip: copy this secret_path into the add-on Options to make it visible:"
    bashio::log.info "  ${SECRET_PATH}"
fi

PORT=9565

# Log the URL
bashio::log.info "============================================"
bashio::log.info "NPM MCP URL (add to your AI client):"
bashio::log.info "  http://<your-ha-ip>:${PORT}/${SECRET_PATH}/sse"
bashio::log.info "============================================"

# Export NPM credentials
export NPM_URL NPM_EMAIL NPM_PASSWORD

# Configure FastMCP via environment variables
export FASTMCP_SSE_PATH="/${SECRET_PATH}/sse"
export FASTMCP_MESSAGE_PATH="/${SECRET_PATH}/messages/"
export FASTMCP_PORT="${PORT}"
export FASTMCP_HOST="0.0.0.0"

bashio::log.info "Starting MCP server on port ${PORT}..."
bashio::log.info "SSE endpoint:  /${SECRET_PATH}/sse"

exec /opt/npm-mcp/venv/bin/python /opt/npm-mcp/server.py
